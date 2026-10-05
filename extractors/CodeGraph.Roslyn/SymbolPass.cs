using CodeGraph.Shared;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace CodeGraph.Roslyn;

/// <summary>
/// Symbol pass (SPEC 5.2): every type and member declared in the project, with containment,
/// inheritance, interface implementation, overrides, P/Invoke declarations and classification.
/// </summary>
internal sealed class SymbolPass(GraphBuilder graph)
{
    private readonly Rules _rules = graph.Rules;

    public void Run(CancellationToken cancellationToken) => VisitNamespace(graph.Compilation.Assembly.GlobalNamespace, cancellationToken);

    private void VisitNamespace(INamespaceSymbol ns, CancellationToken cancellationToken)
    {
        foreach (var member in ns.GetMembers())
        {
            cancellationToken.ThrowIfCancellationRequested();
            if (member is INamespaceSymbol child)
            {
                VisitNamespace(child, cancellationToken);
            }
            else if (member is INamedTypeSymbol type)
            {
                VisitType(type);
            }
        }
    }

    private void VisitType(INamedTypeSymbol type)
    {
        if (type.IsImplicitlyDeclared || graph.Id(type) is not { } id)
        {
            return;
        }

        var (file, start, end) = graph.Span(type);
        var containerId = type.ContainingType is { } container ? graph.Id(container) : null;
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Type",
            Id = id,
            Name = type.Name,
            Fqn = GraphBuilder.Fqn(type),
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = file,
            LineStart = start,
            LineEnd = end,
            TypeId = containerId,
            Kind = KindOf(type),
            Accessibility = AccessibilityOf(type.DeclaredAccessibility),
            IsAbstract = True(type.IsAbstract && type.TypeKind != TypeKind.Interface),
            IsStatic = True(type.IsStatic),
            Attributes = graph.Attributes(type),
            IsPublicApi = True(_rules.IsPublicApi(type)),
        });

        graph.AddEdge("CONTAINS", graph.AssemblyId, id, file, start);
        if (containerId is not null)
        {
            graph.AddEdge("HAS_MEMBER", containerId, id, file, start);
        }

        EmitDefinedIn(type, id);

        if (type.BaseType is { } baseType && !IsImplicitBase(baseType) && graph.Target(baseType) is { } baseId)
        {
            graph.AddEdge("INHERITS", id, baseId, file, start);
        }

        foreach (var iface in type.Interfaces)
        {
            if (graph.Target(iface) is { } ifaceId)
            {
                graph.AddEdge("IMPLEMENTS", id, ifaceId, file, start);
            }
        }

        MapInterfaceImplementations(type);

        foreach (var member in type.GetMembers())
        {
            switch (member)
            {
                case IMethodSymbol method:
                    VisitMethod(method, id);
                    break;
                case IFieldSymbol field when !field.IsImplicitlyDeclared:
                    VisitField(field, id);
                    break;
                case IPropertySymbol property:
                    VisitProperty(property, id);
                    break;
                case IEventSymbol @event:
                    VisitEvent(@event, id);
                    break;
                case INamedTypeSymbol nested:
                    VisitType(nested);
                    break;
            }
        }

        SynthesizeStaticConstructor(type, id, file, start);
    }

    /// <summary>One DEFINED_IN per declaration: a partial class split over two files has two.</summary>
    private void EmitDefinedIn(INamedTypeSymbol type, string id)
    {
        foreach (var reference in type.DeclaringSyntaxReferences)
        {
            var path = graph.RelativePath(reference.SyntaxTree.FilePath);
            var lines = reference.SyntaxTree.GetLineSpan(reference.Span);
            graph.AddEdge("DEFINED_IN", id, Ids.File(path), path, lines.StartLinePosition.Line + 1,
                discriminator: path, lineEnd: lines.EndLinePosition.Line + 1);
        }
    }

    /// <summary>
    /// For each interface method, the method that implements it for this type - including explicit
    /// implementations and implementations inherited from a base class (SPEC 5.2, interface mapping).
    /// </summary>
    private void MapInterfaceImplementations(INamedTypeSymbol type)
    {
        if (type.TypeKind == TypeKind.Interface)
        {
            return;
        }

        foreach (var iface in type.AllInterfaces)
        {
            foreach (var member in iface.GetMembers())
            {
                if (member is not IMethodSymbol { IsStatic: false } declared || !(declared.IsAbstract || declared.IsVirtual))
                {
                    continue;
                }

                if (type.FindImplementationForInterfaceMember(declared) is not IMethodSymbol implementation
                    || SymbolEqualityComparer.Default.Equals(implementation.ContainingType, declared.ContainingType)
                    || !implementation.Locations.Any(l => l.IsInSource))
                {
                    continue; // default interface method, or implemented by an external base class
                }

                // Inherited from a base class that implements the interface itself: the base type emits it.
                var owner = implementation.ContainingType.OriginalDefinition;
                if (!SymbolEqualityComparer.Default.Equals(owner, type.OriginalDefinition)
                    && owner.AllInterfaces.Any(i => SymbolEqualityComparer.Default.Equals(i.OriginalDefinition, iface.OriginalDefinition)))
                {
                    continue;
                }

                if (graph.Id(implementation) is { } implId && graph.Target(declared) is { } declId)
                {
                    var (file, line, _) = graph.Span(implementation);
                    graph.AddEdge("IMPLEMENTS_METHOD", implId, declId, file, line);
                }
            }
        }
    }

    private void VisitMethod(IMethodSymbol method, string typeId)
    {
        if (graph.Id(method) is not { } id)
        {
            return;
        }

        var (file, start, end) = graph.Span(method);
        var entryKind = _rules.EntryKind(method);
        var isTest = _rules.IsTest(method);
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Method",
            Id = id,
            Name = method.Name,
            Fqn = GraphBuilder.Fqn(method),
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = file,
            LineStart = start,
            LineEnd = end,
            TypeId = typeId,
            Signature = method.ToDisplayString(GraphBuilder.SignatureFormat),
            Accessibility = AccessibilityOf(method.DeclaredAccessibility),
            IsStatic = True(method.IsStatic),
            IsVirtual = True(method.IsVirtual),
            IsAbstract = True(method.IsAbstract),
            IsOverride = True(method.IsOverride),
            IsExtern = True(method.IsExtern),
            IsCtor = True(method.MethodKind is MethodKind.Constructor or MethodKind.StaticConstructor),
            IsAccessor = True(method.AssociatedSymbol is not null),
            IsImplicit = True(method.IsImplicitlyDeclared),
            AssociatedId = method.AssociatedSymbol is { } owner ? graph.Id(owner) : null,
            Attributes = graph.Attributes(method),
            IsTest = True(isTest),
            IsEntryPoint = True(entryKind is not null),
            EntryKind = entryKind,
            IsPublicApi = True(_rules.IsPublicApi(method)),
            DelegateParamTypes = method.IsExtern ? DelegateParameterTypes(method) : null,
        });
        graph.AddEdge("HAS_MEMBER", typeId, id, file, start);

        if (method.OverriddenMethod is { } overridden && graph.Target(overridden) is { } overriddenId)
        {
            graph.AddEdge("OVERRIDES", id, overriddenId, file, start);
        }

        if (method.GetDllImportData() is { } import)
        {
            EmitPInvoke(id, method, import, file, start);
        }

        if (method is { IsImplicitlyDeclared: true, MethodKind: MethodKind.Constructor })
        {
            EmitImplicitBaseConstructorCall(method, id, file, start);
        }
    }

    /// <summary>
    /// An implicit default constructor has no syntax for the body pass to bind, yet it calls the base
    /// class's parameterless constructor. Explicit constructors get this edge from the operation tree.
    /// </summary>
    private void EmitImplicitBaseConstructorCall(IMethodSymbol constructor, string id, string? file, int? line)
    {
        var baseType = constructor.ContainingType.BaseType;
        if (baseType is null || baseType.SpecialType == SpecialType.System_Object || constructor.ContainingType.IsValueType)
        {
            return;
        }

        var baseConstructor = baseType.InstanceConstructors.FirstOrDefault(c => c.Parameters.All(p => p.IsOptional || p.IsParams));
        if (baseConstructor is not null && graph.Target(baseConstructor) is { } baseId)
        {
            graph.AddEdge("CALLS", id, baseId, file, line, kind: "ctor", isImplicit: true);
        }
    }

    /// <summary>[DllImport] extern method -> :NativeExport exp:&lt;dll&gt;!&lt;entry point&gt; (SPEC 5.2).</summary>
    private void EmitPInvoke(string methodId, IMethodSymbol method, DllImportData import, string? file, int? line)
    {
        var dll = Ids.NormalizeDll(import.ModuleName ?? "");
        var entryPoint = import.EntryPointName ?? method.Name;
        var exportId = Ids.Export(dll, entryPoint);
        graph.Nodes.Add(new NodeRecord
        {
            Label = "NativeExport",
            Id = exportId,
            Name = entryPoint,
            Fqn = dll + "!" + entryPoint,
            Lang = "cpp",
            Assembly = Path.GetFileNameWithoutExtension(Path.GetFileName(Ids.NormalizePath(import.ModuleName ?? ""))),
            Dll = dll,
            EntryPoint = entryPoint,
        });
        graph.AddEdge("PINVOKES", methodId, exportId, file, line);
    }

    private string[]? DelegateParameterTypes(IMethodSymbol method)
    {
        var ids = method.Parameters
            .Where(p => p.Type.TypeKind == TypeKind.Delegate)
            .Select(p => graph.Target(p.Type))
            .OfType<string>()
            .ToArray();
        return ids.Length > 0 ? ids : null;
    }

    private void VisitField(IFieldSymbol field, string typeId)
    {
        if (graph.Id(field) is not { } id)
        {
            return;
        }

        var (file, start, end) = graph.Span(field);
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Field",
            Id = id,
            Name = field.Name,
            Fqn = GraphBuilder.Fqn(field),
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = file,
            LineStart = start,
            LineEnd = end,
            TypeId = typeId,
            TypeFqn = field.Type.ToDisplayString(GraphBuilder.FqnFormat),
            Accessibility = AccessibilityOf(field.DeclaredAccessibility),
            IsStatic = True(field.IsStatic),
            IsConst = True(field.IsConst),
            ConstValue = field.HasConstantValue ? ConstantText(field.ConstantValue) : null,
            Attributes = graph.Attributes(field),
        });
        graph.AddEdge("HAS_MEMBER", typeId, id, file, start);
    }

    private void VisitProperty(IPropertySymbol property, string typeId)
    {
        if (graph.Id(property) is not { } id)
        {
            return;
        }

        var (file, start, end) = graph.Span(property);
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Property",
            Id = id,
            Name = property.Name,
            Fqn = GraphBuilder.Fqn(property),
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = file,
            LineStart = start,
            LineEnd = end,
            TypeId = typeId,
            Signature = property.ToDisplayString(GraphBuilder.SignatureFormat),
            TypeFqn = property.Type.ToDisplayString(GraphBuilder.FqnFormat),
            Accessibility = AccessibilityOf(property.DeclaredAccessibility),
            IsStatic = True(property.IsStatic),
            HasGetter = property.GetMethod is not null,
            HasSetter = property.SetMethod is not null,
            Attributes = graph.Attributes(property),
            IsPublicApi = True(_rules.IsPublicApi(property)),
        });
        graph.AddEdge("HAS_MEMBER", typeId, id, file, start);
    }

    private void VisitEvent(IEventSymbol @event, string typeId)
    {
        if (graph.Id(@event) is not { } id)
        {
            return;
        }

        var (file, start, end) = graph.Span(@event);
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Event",
            Id = id,
            Name = @event.Name,
            Fqn = GraphBuilder.Fqn(@event),
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = file,
            LineStart = start,
            LineEnd = end,
            TypeId = typeId,
            TypeFqn = @event.Type.ToDisplayString(GraphBuilder.FqnFormat),
            Accessibility = AccessibilityOf(@event.DeclaredAccessibility),
            IsStatic = True(@event.IsStatic),
            Attributes = graph.Attributes(@event),
        });
        graph.AddEdge("HAS_MEMBER", typeId, id, file, start);
    }

    /// <summary>
    /// Static initializers run in a static constructor that only exists in IL when none is declared.
    /// The node gives the body pass somewhere to attach the initializers' calls.
    /// </summary>
    private void SynthesizeStaticConstructor(INamedTypeSymbol type, string typeId, string? file, int? line)
    {
        if (!type.StaticConstructors.IsEmpty || !HasStaticInitializer(type))
        {
            return;
        }

        var id = graph.SynthesizedStaticConstructorId(type);
        graph.Nodes.Add(new NodeRecord
        {
            Label = "Method",
            Id = id,
            Name = ".cctor",
            Fqn = GraphBuilder.Fqn(type) + "..cctor",
            Lang = "cs",
            Assembly = graph.AssemblyName,
            File = file,
            LineStart = line,
            LineEnd = line,
            TypeId = typeId,
            Signature = "static " + type.Name + "()",
            Accessibility = "private",
            IsStatic = true,
            IsCtor = true,
            IsImplicit = true,
        });
        graph.AddEdge("HAS_MEMBER", typeId, id, file, line);
    }

    private static bool HasStaticInitializer(INamedTypeSymbol type) => type.GetMembers().Any(member => member switch
    {
        IFieldSymbol { IsStatic: true, IsConst: false } field => field.DeclaringSyntaxReferences
            .Any(r => r.GetSyntax() is VariableDeclaratorSyntax { Initializer: not null }),
        IPropertySymbol { IsStatic: true } property => property.DeclaringSyntaxReferences
            .Any(r => r.GetSyntax() is PropertyDeclarationSyntax { Initializer: not null }),
        _ => false,
    });

    /// <summary>object, ValueType, Enum and MulticastDelegate are implied by the type kind: not worth an edge.</summary>
    private static bool IsImplicitBase(INamedTypeSymbol baseType) => baseType.SpecialType is
        SpecialType.System_Object or SpecialType.System_ValueType or SpecialType.System_Enum or SpecialType.System_MulticastDelegate;

    private static string KindOf(INamedTypeSymbol type) => type switch
    {
        { IsRecord: true, TypeKind: TypeKind.Struct } => "record struct",
        { IsRecord: true } => "record",
        _ => type.TypeKind switch
        {
            TypeKind.Class => "class",
            TypeKind.Struct => "struct",
            TypeKind.Interface => "interface",
            TypeKind.Enum => "enum",
            TypeKind.Delegate => "delegate",
            _ => type.TypeKind.ToString().ToLowerInvariant(),
        },
    };

    private static string AccessibilityOf(Accessibility accessibility) => accessibility switch
    {
        Accessibility.Public => "public",
        Accessibility.Internal => "internal",
        Accessibility.Protected => "protected",
        Accessibility.ProtectedOrInternal => "protected internal",
        Accessibility.ProtectedAndInternal => "private protected",
        Accessibility.Private => "private",
        _ => "unknown",
    };

    private static string? ConstantText(object? value)
    {
        var text = Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture);
        return text is { Length: > 1000 } ? text[..1000] : text;
    }

    /// <summary>Flags are written only when true; the loader fills false (keeps the files small).</summary>
    private static bool? True(bool value) => value ? true : null;
}
