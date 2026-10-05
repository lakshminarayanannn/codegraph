using Mono.Cecil;
using Mono.Cecil.Cil;

// Rewrites every method marked [SimIjw("Name.In.Metadata")] the way MSVC /clr emits a call into native code
// compiled in the same image ("IJW"):
//   * a static method of <Module> (the global type), named "<namespace>.<class>.<method>" ({ctor}/{dtor} for
//     constructors/destructors),
//   * attributes pinvokeimpl with NO ImplMap row ("pinvokeimpl(/* No map */)" in ildasm), native unmanaged preservesig,
//   * SuppressUnmanagedCodeSecurity.
// Call sites are redirected to the new method. The SimIjw attribute type is removed. See docs/DECISIONS.md D-10.
if (args.Length != 1)
{
    Console.Error.WriteLine("usage: SimIjwRewriter <MiniPlant.Interop.dll>");
    return 64;
}

var path = Path.GetFullPath(args[0]);
var module = ModuleDefinition.ReadModule(path, new ReaderParameters { InMemory = true });
var globalType = module.Types.First(t => t.Name == "<Module>");
// Reference the attribute in the module's own core library (mscorlib), not the rewriter's runtime.
var corlib = module.AssemblyReferences.First(r => r.Name is "mscorlib" or "netstandard" or "System.Runtime");
var suppressType = new TypeReference("System.Security", "SuppressUnmanagedCodeSecurityAttribute", module, corlib);
var suppress = new MethodReference(".ctor", module.TypeSystem.Void, suppressType) { HasThis = true };
var replaced = new Dictionary<MethodDefinition, MethodDefinition>();

foreach (var type in module.Types.ToList())
{
    foreach (var method in type.Methods.ToList())
    {
        var marker = method.CustomAttributes.FirstOrDefault(a => a.AttributeType.Name == "SimIjwAttribute");
        if (marker is null)
        {
            continue;
        }

        // A brand-new definition: an existing one would re-read its ImplMap row from the image when written.
        var stub = new MethodDefinition((string)marker.ConstructorArguments[0].Value,
            MethodAttributes.Assembly | MethodAttributes.Static | MethodAttributes.PInvokeImpl | MethodAttributes.HideBySig,
            method.ReturnType)
        {
            ImplAttributes = MethodImplAttributes.Native | MethodImplAttributes.Unmanaged | MethodImplAttributes.PreserveSig,
        };
        foreach (var parameter in method.Parameters)
        {
            stub.Parameters.Add(new ParameterDefinition(parameter.Name, parameter.Attributes, parameter.ParameterType));
        }

        stub.CustomAttributes.Add(new CustomAttribute(suppress));
        globalType.Methods.Add(stub);
        type.Methods.Remove(method);
        replaced[method] = stub;
    }
}

foreach (var method in module.Types.SelectMany(t => t.Methods).Where(m => m.HasBody))
{
    foreach (var instruction in method.Body.Instructions)
    {
        if (instruction.Operand is MethodDefinition target && replaced.TryGetValue(target, out var stub))
        {
            instruction.Operand = stub;
        }
    }
}

var markerType = module.Types.FirstOrDefault(t => t.Name == "SimIjwAttribute");
if (markerType is not null)
{
    module.Types.Remove(markerType);
}

module.Write(path);
Console.Error.WriteLine($"SimIjwRewriter: {replaced.Count} IJW stub(s) written to {path}");
return 0;
