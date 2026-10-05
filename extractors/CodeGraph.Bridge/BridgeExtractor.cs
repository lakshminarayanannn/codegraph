using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using CodeGraph.Shared;
using ICSharpCode.Decompiler.Documentation;
using ICSharpCode.Decompiler.Metadata;
using ICSharpCode.Decompiler.TypeSystem;

namespace CodeGraph.Bridge;

/// <summary>
/// Extracts one C++/CLI assembly (SPEC 7.3):
///  * managed symbols with "dn:" IDs from ILSpy's IdStringProvider, identical to Roslyn's documentation IDs
///    (verified by the --ids-only check), so C# callers join onto these nodes with no matching logic;
///  * IL scan: managed calls, field reads/writes, and calls of pinvokeimpl methods - with an ImplMap row
///    that is a P/Invoke into a DLL (PINVOKES); without one ("No map") it is IJW native code in this image
///    (CALLS_NATIVE, the native name resolved later by the loader);
///  * WRAPS: a managed field holding a pointer to a native class (IJW marks those value types [NativeCppClass]).
/// </summary>
internal sealed class BridgeExtractor
{
    private static readonly string[] NativeProxyAttributes =
    {
        "System.Runtime.CompilerServices.NativeCppClassAttribute",
        "System.Runtime.CompilerServices.MiscellaneousBitsAttribute",
        "System.Runtime.CompilerServices.DebugInfoInPDBAttribute",
    };

    private readonly CodeGraphConfig _config;
    private readonly CppCliAssembly _target;
    private readonly string _outDir;
    private readonly NodeSet _nodes = new();
    private readonly EdgeAggregator _edges = new();
    private readonly HashSet<string> _nativeProxies = new(StringComparer.Ordinal);
    private readonly Dictionary<string, NodeRecord> _methodNodes = new(StringComparer.Ordinal);
    private int _nativeCalls, _indirectNativeCalls, _pinvokes, _unresolvedTokens;

    private PEFile _file = null!;
    private MetadataReader _metadata = null!;
    private MetadataModule _module = null!;
    private string _owner = "";
    private string _location = "";
    private SourceLocator? _sources;

    public BridgeExtractor(CodeGraphConfig config, CppCliAssembly target, string outDir)
    {
        _config = config;
        _target = target;
        _outDir = outDir;
    }

    /// <summary>Every member ID the extractor would emit, for the ID-equality check against Roslyn.</summary>
    public static IEnumerable<string> ManagedIds(string path)
    {
        var extractor = new BridgeExtractor(new CodeGraphConfig(), new CppCliAssembly(path, Path.GetFileNameWithoutExtension(path), null), "");
        using var file = extractor.Open();
        foreach (var type in extractor.ManagedTypes())
        {
            yield return extractor.Id(type);
            foreach (var member in extractor.Members(type))
            {
                yield return extractor.Id(member);
            }
        }
    }

    /// <summary>
    /// Phase 0 spike (SPEC 7.3 step 1): one line per pinvokeimpl method - declaring type, metadata name,
    /// whether an ImplMap row exists (P/Invoke into a DLL) or not (IJW code in this image), and its flags.
    /// </summary>
    public static void ListNativeStubs(string path)
    {
        using var file = new PEFile(path, PEStreamOptions.PrefetchEntireImage);
        var metadata = file.Metadata;
        Console.WriteLine("declaringType\tname\timplMap\tmodule\timportName\tparameters\tmethodAttributes\timplAttributes");
        foreach (var handle in metadata.MethodDefinitions)
        {
            var method = metadata.GetMethodDefinition(handle);
            if ((method.Attributes & MethodAttributes.PinvokeImpl) == 0)
            {
                continue;
            }

            var type = metadata.GetString(metadata.GetTypeDefinition(method.GetDeclaringType()).Name);
            var import = method.GetImport();
            var module = import.Module.IsNil ? "" : metadata.GetString(metadata.GetModuleReference(import.Module).Name);
            var importName = import.Name.IsNil ? "" : metadata.GetString(import.Name);
            var signature = metadata.GetBlobReader(method.Signature);
            if (signature.ReadSignatureHeader().IsGeneric)
            {
                signature.ReadCompressedInteger(); // generic parameter count precedes the parameter count
            }

            var parameters = signature.ReadCompressedInteger();
            Console.WriteLine(string.Join("\t", type, metadata.GetString(method.Name), import.Module.IsNil ? "no" : "yes",
                module, importName, parameters, method.Attributes, Describe(method.ImplAttributes)));
        }
    }

    /// <summary>"Native, Unmanaged, PreserveSig" - what ildasm prints as "native unmanaged preservesig".</summary>
    private static string Describe(MethodImplAttributes attributes)
    {
        var parts = new List<string> { (attributes & MethodImplAttributes.CodeTypeMask).ToString() };
        parts.Add((attributes & MethodImplAttributes.Unmanaged) != 0 ? "Unmanaged" : "Managed");
        if ((attributes & MethodImplAttributes.PreserveSig) != 0)
        {
            parts.Add("PreserveSig");
        }

        if ((attributes & MethodImplAttributes.InternalCall) != 0)
        {
            parts.Add("InternalCall");
        }

        return string.Join(", ", parts);
    }

    public Dictionary<string, object?> Extract()
    {
        using var file = Open();
        _location = _target.SourceDir is { } dir ? Ids.RelativeTo(_config.RepoRootFull, dir) : Ids.RelativeTo(_config.RepoRootFull, Path.GetDirectoryName(_target.Path)!);
        _sources = _target.SourceDir is { } sourceDir ? SourceLocator.Build(sourceDir, _config.RepoRootFull) : null;

        EmitAssembly();
        var types = ManagedTypes().ToList();
        foreach (var type in types)
        {
            EmitType(type);
        }

        EmitGlobalFunctions();

        // Bodies of every method with IL, including managed global functions in <Module>.
        foreach (var handle in _metadata.MethodDefinitions)
        {
            ScanBody(handle);
        }

        var nodes = _nodes.Drain();
        var edges = _edges.Drain();
        var report = new Dictionary<string, object?>
        {
            ["owner"] = _owner,
            ["path"] = _target.Path,
            ["language"] = "cppcli",
            ["nodes"] = nodes.Count,
            ["edges"] = edges.Count,
            ["nativeCalls"] = _nativeCalls,
            ["indirectNativeCalls"] = _indirectNativeCalls,
            ["pinvokes"] = _pinvokes,
            ["unresolvedTokens"] = _unresolvedTokens,
            ["nativeProxyTypes"] = _nativeProxies.Count,
            ["extractedAt"] = DateTimeOffset.UtcNow.ToString("O"),
        };
        JsonlWriter.WriteOwner(_outDir, _owner, nodes, edges, report);
        return report;
    }

    private PEFile Open()
    {
        _file = new PEFile(_target.Path, PEStreamOptions.PrefetchEntireImage);
        _metadata = _file.Metadata;
        var resolver = new UniversalAssemblyResolver(_target.Path, false, _file.DetectTargetFrameworkId());
        _module = new DecompilerTypeSystem(_file, resolver).MainModule;
        _owner = _file.Name;
        foreach (var type in _module.TypeDefinitions.Where(IsNativeProxy))
        {
            _nativeProxies.Add(type.FullName);
        }

        return _file;
    }

    // ---- symbols -------------------------------------------------------------------------------

    private IEnumerable<ITypeDefinition> ManagedTypes() =>
        _module.TypeDefinitions.Where(t => t.Name != "<Module>" && !IsNativeProxy(t) && !IsCompilerGenerated(t));

    private IEnumerable<IEntity> Members(ITypeDefinition type)
    {
        var definition = _metadata.GetTypeDefinition((TypeDefinitionHandle)type.MetadataToken);
        foreach (var handle in definition.GetMethods())
        {
            if (_module.GetDefinition(handle) is { } method && !IsGeneratedName(method.Name))
            {
                yield return method;
            }
        }

        foreach (var field in type.Fields.Where(f => !IsGeneratedName(f.Name) && !IsCompilerGeneratedMember(f)))
        {
            yield return field;
        }

        foreach (var property in type.Properties)
        {
            yield return property;
        }

        foreach (var @event in type.Events)
        {
            yield return @event;
        }
    }

    private void EmitAssembly()
    {
        var framework = _module.GetAssemblyAttributes()
            .FirstOrDefault(a => a.AttributeType.FullName == "System.Runtime.Versioning.TargetFrameworkAttribute")
            ?.FixedArguments.FirstOrDefault().Value as string;
        _nodes.Add(new NodeRecord
        {
            Label = "Assembly", Id = Ids.Assembly(_owner), Name = _owner, Fqn = _owner, Lang = "cppcli",
            Assembly = _owner, File = _location, Kind = "cppcli", TargetFramework = framework,
        });

        // References to other assemblies; the loader keeps only those that are indexed.
        foreach (var handle in _metadata.AssemblyReferences)
        {
            var name = _metadata.GetString(_metadata.GetAssemblyReference(handle).Name);
            Edge("REFERENCES", Ids.Assembly(_owner), Ids.Assembly(name), kind: "binary");
        }
    }

    private void EmitType(ITypeDefinition type)
    {
        var id = Id(type);
        var (file, line) = Locate(type.Name, null);
        _nodes.Add(new NodeRecord
        {
            Label = "Type", Id = id, Name = type.Name, Fqn = type.FullName, Lang = "cppcli", Assembly = _owner,
            File = file, LineStart = line, LineEnd = line, LocationConfidence = line > 0 ? "heuristic" : null,
            TypeId = type.DeclaringTypeDefinition is { } outer ? Id(outer) : null,
            Kind = KindOf(type), Accessibility = AccessibilityOf(type.Accessibility),
            IsAbstract = True(type.IsAbstract && type.Kind != TypeKind.Interface), IsStatic = True(type.IsStatic),
            Attributes = Attributes(type),
        });
        Edge("CONTAINS", Ids.Assembly(_owner), id);
        if (type.DeclaringTypeDefinition is { } declaring)
        {
            Edge("HAS_MEMBER", Id(declaring), id);
        }

        foreach (var baseType in type.DirectBaseTypes)
        {
            if (baseType.Kind == TypeKind.Interface)
            {
                Edge("IMPLEMENTS", id, TypeId(baseType));
            }
            else if (!IsImplicitBase(baseType))
            {
                Edge("INHERITS", id, TypeId(baseType));
            }
        }

        foreach (var member in Members(type))
        {
            switch (member)
            {
                case IMethod method:
                    EmitMethod(method, id);
                    break;
                case IField field:
                    EmitField(field, id);
                    break;
                case IProperty property:
                    EmitMember("Property", property, id, property.ReturnType);
                    break;
                case IEvent @event:
                    EmitMember("Event", @event, id, @event.ReturnType);
                    break;
            }
        }
    }

    private void EmitMethod(IMethod method, string typeId)
    {
        var id = Id(method);
        var definition = _metadata.GetMethodDefinition((MethodDefinitionHandle)method.MetadataToken);
        var isPinvoke = (definition.Attributes & MethodAttributes.PinvokeImpl) != 0;
        var (file, line) = Locate(method.DeclaringType.Name, SourceName(method));
        var node = new NodeRecord
        {
            Label = "Method", Id = id, Name = method.Name, Fqn = method.DeclaringType.FullName + "." + method.Name,
            Lang = "cppcli", Assembly = _owner, File = file, LineStart = line, LineEnd = line,
            LocationConfidence = line > 0 ? "heuristic" : null, TypeId = typeId, Signature = Signature(method),
            Accessibility = AccessibilityOf(method.Accessibility), IsStatic = True(method.IsStatic),
            IsVirtual = True(method.IsVirtual && !method.IsOverride), IsAbstract = True(method.IsAbstract),
            IsOverride = True(method.IsOverride), IsExtern = True(isPinvoke), IsCtor = True(method.IsConstructor),
            IsAccessor = True(method.IsAccessor), AssociatedId = method.AccessorOwner is { } owner ? Id(owner) : null,
            Attributes = Attributes(method),
        };
        _nodes.Add(node);
        _methodNodes[id] = node;
        Edge("HAS_MEMBER", typeId, id);

        // Base members: the overridden method, and interface methods this one implements (explicitly or by name).
        if (method.IsOverride && InheritanceHelper.GetBaseMember(method) is IMethod overridden && overridden.DeclaringType.Kind != TypeKind.Interface)
        {
            Edge("OVERRIDES", id, Id(overridden.MemberDefinition));
        }

        foreach (var implemented in InheritanceHelper.GetBaseMembers(method, includeImplementedInterfaces: true)
                     .Where(m => m.DeclaringType.Kind == TypeKind.Interface))
        {
            Edge("IMPLEMENTS_METHOD", id, Id(implemented.MemberDefinition));
        }

        if (isPinvoke && definition.GetImport() is { Module.IsNil: false } import)
        {
            EmitPInvoke(id, method.Name, import);
        }
    }

    /// <summary>Managed global functions (in &lt;Module&gt;): real code wrappers often call, so they get nodes too.</summary>
    private void EmitGlobalFunctions()
    {
        var global = _module.TypeDefinitions.FirstOrDefault(t => t.Name == "<Module>");
        if (global is null)
        {
            return;
        }

        foreach (var handle in _metadata.GetTypeDefinition((TypeDefinitionHandle)global.MetadataToken).GetMethods())
        {
            var definition = _metadata.GetMethodDefinition(handle);
            if ((definition.Attributes & MethodAttributes.PinvokeImpl) != 0 || _module.GetDefinition(handle) is not { } method || !IsScanned(method))
            {
                continue;
            }

            var id = MemberId(method);
            var (file, line) = Locate("", method.Name);
            var node = new NodeRecord
            {
                Label = "Method", Id = id, Name = method.Name, Fqn = method.Name, Lang = "cppcli", Assembly = _owner,
                File = file, LineStart = line, LineEnd = line, LocationConfidence = line > 0 ? "heuristic" : null,
                Signature = Signature(method), Accessibility = AccessibilityOf(method.Accessibility), IsStatic = true,
            };
            _nodes.Add(node);
            _methodNodes[id] = node;
        }
    }

    private void EmitField(IField field, string typeId)
    {
        var id = Id(field);
        _nodes.Add(new NodeRecord
        {
            Label = "Field", Id = id, Name = field.Name, Fqn = field.DeclaringType.FullName + "." + field.Name,
            Lang = "cppcli", Assembly = _owner, File = _location, LineStart = -1, TypeId = typeId,
            TypeFqn = field.Type.FullName, Accessibility = AccessibilityOf(field.Accessibility),
            IsStatic = True(field.IsStatic), IsConst = True(field.IsConst),
        });
        Edge("HAS_MEMBER", typeId, id);

        // A pointer to a native class: this managed type wraps that native type (SPEC 7.3 step 5).
        if (field.Type is PointerType { ElementType: { } element } && _nativeProxies.Contains(element.FullName))
        {
            _edges.Add(new EdgeRecord
            {
                Type = "WRAPS", Start = typeId, End = "cpp:unresolved-type:" + element.FullName, Owner = _owner,
                File = _location, NativeName = element.FullName,
            });
        }
    }

    private void EmitMember(string label, IMember member, string typeId, IType type)
    {
        var id = Id(member);
        _nodes.Add(new NodeRecord
        {
            Label = label, Id = id, Name = member.Name, Fqn = member.DeclaringType.FullName + "." + member.Name,
            Lang = "cppcli", Assembly = _owner, File = _location, LineStart = -1, TypeId = typeId,
            TypeFqn = type.FullName, Accessibility = AccessibilityOf(member.Accessibility), IsStatic = True(member.IsStatic),
        });
        Edge("HAS_MEMBER", typeId, id);
    }

    private void EmitPInvoke(string sourceId, string methodName, MethodImport import)
    {
        var dll = _metadata.GetString(_metadata.GetModuleReference(import.Module).Name);
        var entryPoint = import.Name.IsNil ? methodName : _metadata.GetString(import.Name);
        var exportId = Ids.Export(dll, entryPoint);
        _nodes.Add(new NodeRecord
        {
            Label = "NativeExport", Id = exportId, Name = entryPoint, Fqn = Ids.NormalizeDll(dll) + "!" + entryPoint,
            Lang = "cpp", Assembly = Path.GetFileNameWithoutExtension(dll), Dll = Ids.NormalizeDll(dll), EntryPoint = entryPoint,
        });
        Edge("PINVOKES", sourceId, exportId);
        _pinvokes++;
    }

    // ---- IL scan -------------------------------------------------------------------------------

    private void ScanBody(MethodDefinitionHandle handle)
    {
        var definition = _metadata.GetMethodDefinition(handle);
        if (definition.RelativeVirtualAddress == 0
            || (definition.ImplAttributes & MethodImplAttributes.CodeTypeMask) != MethodImplAttributes.IL
            || _module.GetDefinition(handle) is not { } method
            || !IsScanned(method))
        {
            return; // no body, or native code compiled into the image
        }

        var sourceId = MemberId(method);
        var context = new GenericContext(method.DeclaringTypeDefinition?.TypeParameters, method.TypeParameters);
        var body = _file.Reader.GetMethodBody(definition.RelativeVirtualAddress);
        var indirect = 0;
        foreach (var reference in IlScanner.References(body))
        {
            try
            {
                switch (reference.OpCode)
                {
                    case ILOpCode.Calli:
                        if (IlScanner.IsUnmanagedCalli(_metadata, reference.Token))
                        {
                            indirect++;
                        }

                        break;
                    case ILOpCode.Ldfld or ILOpCode.Ldsfld:
                        FieldAccess(sourceId, reference, context, "READS");
                        break;
                    case ILOpCode.Stfld or ILOpCode.Stsfld:
                        FieldAccess(sourceId, reference, context, "WRITES");
                        break;
                    case ILOpCode.Ldflda or ILOpCode.Ldsflda:
                        FieldAccess(sourceId, reference, context, "READS");
                        FieldAccess(sourceId, reference, context, "WRITES");
                        break;
                    default:
                        Call(sourceId, reference, context);
                        break;
                }
            }
            catch (Exception ex) when (ex is BadImageFormatException or InvalidOperationException or ArgumentException)
            {
                _unresolvedTokens++; // a token the type system cannot resolve: count it, keep going
            }
        }

        if (indirect > 0)
        {
            // Virtual native calls (calli through a vtable) have no target in IL: record that they exist.
            _indirectNativeCalls += indirect;
            if (_methodNodes.TryGetValue(sourceId, out var node))
            {
                node.IndirectNativeCalls = indirect;
            }
        }
    }

    private void Call(string sourceId, IlReference reference, GenericContext context)
    {
        var handle = MetadataTokens.EntityHandle(reference.Token);
        if (handle.Kind == HandleKind.MethodDefinition)
        {
            var definition = _metadata.GetMethodDefinition((MethodDefinitionHandle)handle);
            if ((definition.Attributes & MethodAttributes.PinvokeImpl) != 0 && IsGlobal(definition))
            {
                NativeCall(sourceId, (MethodDefinitionHandle)handle, definition, reference.Offset);
                return;
            }
        }

        if (_module.ResolveMethod(handle, context) is not { } target)
        {
            _unresolvedTokens++;
            return;
        }

        var declared = (IMethod)target.MemberDefinition;
        var targetId = MemberId(declared);
        if (reference.OpCode is ILOpCode.Ldftn or ILOpCode.Ldvirtftn)
        {
            Edge("REFERENCES_METHOD", sourceId, targetId, ilOffset: reference.Offset);
            return;
        }

        var kind = declared.IsConstructor ? "ctor"
            : declared.IsAccessor && declared.AccessorKind == System.Reflection.MethodSemanticsAttributes.Getter ? "getter"
            : declared.IsAccessor && declared.AccessorKind == System.Reflection.MethodSemanticsAttributes.Setter ? "setter"
            : "call";
        Edge("CALLS", sourceId, targetId, kind: kind, ilOffset: reference.Offset);
        if (declared.AccessorOwner is IProperty property && kind is "getter" or "setter")
        {
            Edge(kind == "getter" ? "READS" : "WRITES", sourceId, Id(property.MemberDefinition), ilOffset: reference.Offset);
        }
    }

    /// <summary>A call of a pinvokeimpl method of &lt;Module&gt;: IJW native code, or a P/Invoke of an imported function.</summary>
    private void NativeCall(string sourceId, MethodDefinitionHandle handle, MethodDefinition definition, int offset)
    {
        var name = _metadata.GetString(definition.Name);
        var import = definition.GetImport();
        if (!import.Module.IsNil)
        {
            EmitPInvoke(sourceId, name, import); // the caller P/Invokes the DLL export directly
            return;
        }

        // "No map": native code in this image. The loader matches the name against the native extractor's symbols.
        _nativeCalls++;
        var stub = _module.GetDefinition(handle);
        var parameterCount = stub?.Parameters.Count ?? 0;
        var scope = name.Contains('.') ? name[..name.LastIndexOf('.')] : "";
        var isInstance = stub?.Parameters.FirstOrDefault()?.Type is PointerType { ElementType: { } self } && self.FullName == scope;
        _edges.Add(new EdgeRecord
        {
            Type = "CALLS_NATIVE", Start = sourceId, End = "cpp:unresolved:" + name, Owner = _owner, File = _location,
            IlOffset = offset, NativeName = name, NativeParamCount = isInstance ? parameterCount - 1 : parameterCount,
        });
    }

    private void FieldAccess(string sourceId, IlReference reference, GenericContext context, string rel)
    {
        if (_module.ResolveEntity(MetadataTokens.EntityHandle(reference.Token), context) is IField field
            && !IsGeneratedName(field.Name) && !_nativeProxies.Contains(field.DeclaringType.FullName))
        {
            Edge(rel, sourceId, Id(field.MemberDefinition), ilOffset: reference.Offset);
        }
    }

    // ---- helpers -------------------------------------------------------------------------------

    private void Edge(string type, string start, string end, string? kind = null, int? ilOffset = null)
    {
        _edges.Add(new EdgeRecord { Type = type, Start = start, End = end, Owner = _owner, File = _location, Kind = kind, IlOffset = ilOffset });
    }

    private string Id(IEntity entity) => Ids.Dn(IdStringProvider.GetIdString(entity));

    /// <summary>Managed global functions live in &lt;Module&gt;, which every C++/CLI assembly has: qualify them by assembly.</summary>
    private string MemberId(IMember member) =>
        member.DeclaringTypeDefinition is { Name: "<Module>" } global && global.ParentModule == _module
            ? Ids.Dn("M:<Module:" + _owner + ">" + IdStringProvider.GetIdString(member)["M:<Module>".Length..])
            : Id(member);

    private bool IsScanned(IMethod method) =>
        method.DeclaringTypeDefinition is { } type
        && (type.Name == "<Module>" ? !IsGeneratedName(method.Name) && !method.Name.StartsWith('_') : !IsNativeProxy(type) && !IsCompilerGenerated(type));

    private static string TypeId(IType type)
    {
        if (type.GetDefinition() is { } definition)
        {
            return Ids.Dn(IdStringProvider.GetIdString(definition));
        }

        var generic = type is ParameterizedType parameterized ? parameterized.GenericType : type;
        return Ids.Dn("T:" + generic.ReflectionName.Replace('+', '.'));
    }

    private bool IsGlobal(MethodDefinition definition) =>
        _metadata.GetString(_metadata.GetTypeDefinition(definition.GetDeclaringType()).Name) == "<Module>";

    private static bool IsNativeProxy(ITypeDefinition type) =>
        type.Kind == TypeKind.Struct && type.GetAttributes().Any(a => NativeProxyAttributes.Contains(a.AttributeType.FullName));

    private static bool IsCompilerGenerated(ITypeDefinition type) =>
        type.Namespace.StartsWith('<') || IsGeneratedName(type.Name)
        || type.GetAttributes().Any(a => a.AttributeType.FullName == "System.Runtime.CompilerServices.CompilerGeneratedAttribute");

    private static bool IsCompilerGeneratedMember(IEntity entity) =>
        entity.GetAttributes().Any(a => a.AttributeType.FullName == "System.Runtime.CompilerServices.CompilerGeneratedAttribute");

    private static bool IsGeneratedName(string name) => name.Length == 0 || name[0] is '<' or '$' or '?' || name.Contains("$");

    private static bool IsImplicitBase(IType type) =>
        type.IsKnownType(KnownTypeCode.Object) || type.IsKnownType(KnownTypeCode.ValueType)
        || type.IsKnownType(KnownTypeCode.Enum) || type.IsKnownType(KnownTypeCode.MulticastDelegate);

    /// <summary>The name a member has in C++/CLI source: constructors use the class name, Finalize is !Class, Dispose ~Class.</summary>
    private static string SourceName(IMethod method) => method.Name switch
    {
        ".ctor" or ".cctor" => method.DeclaringType.Name,
        "Finalize" => "!" + method.DeclaringType.Name,
        "Dispose" when method.Parameters.Count == 0 => "~" + method.DeclaringType.Name,
        _ => method.Name,
    };

    private (string File, int Line) Locate(string typeName, string? memberName) =>
        _sources?.Find(typeName, memberName) is { } found ? found : (_location, -1);

    private static string Signature(IMethod method) =>
        $"{method.ReturnType.Name} {method.Name}({string.Join(", ", method.Parameters.Select(p => $"{p.Type.Name} {p.Name}"))})";

    private static string[]? Attributes(IEntity entity)
    {
        var names = entity.GetAttributes().Select(a => a.AttributeType.FullName).Distinct().ToArray();
        return names.Length > 0 ? names : null;
    }

    private static string KindOf(ITypeDefinition type) => type.Kind switch
    {
        TypeKind.Interface => "interface",
        TypeKind.Struct => "struct",
        TypeKind.Enum => "enum",
        TypeKind.Delegate => "delegate",
        _ => "class",
    };

    private static string AccessibilityOf(Accessibility accessibility) => accessibility switch
    {
        Accessibility.Public => "public",
        Accessibility.Internal => "internal",
        Accessibility.Protected => "protected",
        Accessibility.ProtectedOrInternal => "protected internal",
        Accessibility.ProtectedAndInternal => "private protected",
        _ => "private",
    };

    private static bool? True(bool value) => value ? true : null;
}
