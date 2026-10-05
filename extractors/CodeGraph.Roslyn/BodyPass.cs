using CodeGraph.Shared;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;
using Microsoft.CodeAnalysis.Operations;

namespace CodeGraph.Roslyn;

/// <summary>
/// Body pass (SPEC 5.3) over one syntax tree: binds every method body, accessor, constructor and
/// initializer, and walks the bound operation tree (not the syntax), so overload resolution,
/// extension methods and implicit conversions are already decided by the compiler.
/// One instance per tree, used by one thread.
/// </summary>
internal sealed class BodyPass
{
    private readonly GraphBuilder _graph;
    private readonly SemanticModel _model;
    private readonly string _file;

    // Method groups passed to native code (DllImport or C++/CLI): their REFERENCES_METHOD edges are tagged.
    private readonly Dictionary<IOperation, string> _nativeCallbacks = new(ReferenceEqualityComparer.Instance);

    public BodyPass(GraphBuilder graph, SemanticModel model)
    {
        _graph = graph;
        _model = model;
        _file = graph.RelativePath(model.SyntaxTree.FilePath);
    }

    /// <summary>Count of operations the compiler could not bind (IInvalidOperation): each is a possibly missing edge.</summary>
    public int InvalidOperations { get; private set; }

    /// <summary>Who an operation is attributed to (several constructors for an initializer), and through what.</summary>
    private readonly record struct Context(string[] Owners, string? Via);

    public void Run(CancellationToken cancellationToken)
    {
        foreach (var member in Members(_model.SyntaxTree.GetRoot(cancellationToken)))
        {
            cancellationToken.ThrowIfCancellationRequested();
            VisitMember(member);
        }
    }

    /// <summary>Member declarations, descending into namespaces and types but never into method bodies.</summary>
    private static IEnumerable<MemberDeclarationSyntax> Members(SyntaxNode node)
    {
        var members = node switch
        {
            CompilationUnitSyntax unit => unit.Members,
            BaseNamespaceDeclarationSyntax ns => ns.Members,
            TypeDeclarationSyntax type => type.Members,
            _ => default,
        };
        foreach (var member in members)
        {
            yield return member;
            if (member is BaseNamespaceDeclarationSyntax or TypeDeclarationSyntax)
            {
                foreach (var inner in Members(member))
                {
                    yield return inner;
                }
            }
        }
    }

    private void VisitMember(MemberDeclarationSyntax member)
    {
        switch (member)
        {
            case BaseMethodDeclarationSyntax method when method.Body is not null || method.ExpressionBody is not null:
                // Constructors bind with their initializer, including the implicit ": base()" call.
                if (_model.GetDeclaredSymbol(method) is IMethodSymbol symbol && _graph.Id(symbol) is { } id)
                {
                    WalkBody(method, method.Body, method.ExpressionBody, new Context(new[] { id }, null));
                }

                break;

            case BasePropertyDeclarationSyntax property:
                VisitProperty(property);
                break;

            case BaseFieldDeclarationSyntax field when !field.Modifiers.Any(m => m.IsKind(SyntaxKind.ConstKeyword)):
                // Const values are compile-time constants: their "initializer" never runs.
                foreach (var variable in field.Declaration.Variables)
                {
                    if (variable.Initializer is not null && _model.GetDeclaredSymbol(variable) is { } declared)
                    {
                        var owners = _graph.InitializerOwners(declared.ContainingType, declared.IsStatic);
                        Walk(_model.GetOperation(variable.Initializer), new Context(owners, null));
                    }
                }

                break;
        }
    }

    private void VisitProperty(BasePropertyDeclarationSyntax property)
    {
        if (_model.GetDeclaredSymbol(property) is not IPropertySymbol and not IEventSymbol)
        {
            return;
        }

        var symbol = _model.GetDeclaredSymbol(property)!;

        // int P => expr;  (the arrow belongs to the property, the code runs in the getter)
        var arrow = property switch
        {
            PropertyDeclarationSyntax p => p.ExpressionBody,
            IndexerDeclarationSyntax i => i.ExpressionBody,
            _ => null,
        };
        if (arrow is not null && symbol is IPropertySymbol { GetMethod: { } getter } && _graph.Id(getter) is { } getterId)
        {
            Walk(_model.GetOperation(arrow), new Context(new[] { getterId }, null));
        }

        foreach (var accessor in property.AccessorList?.Accessors ?? default)
        {
            if ((accessor.Body is not null || accessor.ExpressionBody is not null)
                && _model.GetDeclaredSymbol(accessor) is IMethodSymbol accessorSymbol && _graph.Id(accessorSymbol) is { } accessorId)
            {
                WalkBody(accessor, accessor.Body, accessor.ExpressionBody, new Context(new[] { accessorId }, null));
            }
        }

        if (property is PropertyDeclarationSyntax { Initializer: { } initializer })
        {
            var owners = _graph.InitializerOwners(symbol.ContainingType, symbol.IsStatic);
            Walk(_model.GetOperation(initializer), new Context(owners, null));
        }
    }

    /// <summary>Binds a whole member (includes ": base(...)" for constructors); falls back to the body parts.</summary>
    private void WalkBody(SyntaxNode declaration, SyntaxNode? block, SyntaxNode? arrow, Context context)
    {
        var operation = _model.GetOperation(declaration);
        if (operation is not null)
        {
            Walk(operation, context);
            return;
        }

        Walk(block is null ? null : _model.GetOperation(block), context);
        Walk(arrow is null ? null : _model.GetOperation(arrow), context);
    }

    private void Walk(IOperation? operation, Context context)
    {
        if (operation is null)
        {
            return;
        }

        switch (operation)
        {
            case INameOfOperation:
                return; // nameof(x) is a compile-time string, not a use of x
            case IAnonymousFunctionOperation lambda:
                WalkChildren(lambda, context with { Via = "lambda" });
                return;
            case ILocalFunctionOperation local:
                WalkChildren(local, context with { Via = "localfunc" });
                return;
            case IInvocationOperation invocation:
                OnInvocation(invocation, context);
                break;
            case IObjectCreationOperation creation:
                OnCreation(creation, context);
                break;
            case IPropertyReferenceOperation property:
                OnProperty(property, context);
                break;
            case IFieldReferenceOperation field:
                OnField(field, context);
                break;
            case IEventAssignmentOperation eventAssignment:
                OnEventAssignment(eventAssignment, context);
                break;
            case IMethodReferenceOperation methodReference:
                OnMethodReference(methodReference, context);
                break;
            case IThrowOperation @throw:
                OnThrow(@throw, context);
                break;
            case IFieldInitializerOperation initializer:
                foreach (var field in initializer.InitializedFields)
                {
                    EmitWrite(field, initializer.Syntax, context);
                }

                break;
            case IPropertyInitializerOperation initializer:
                foreach (var property in initializer.InitializedProperties)
                {
                    EmitWrite(property, initializer.Syntax, context);
                }

                break;
            case IInvalidOperation:
                InvalidOperations++;
                break;
        }

        WalkChildren(operation, context);
    }

    private void WalkChildren(IOperation operation, Context context)
    {
        foreach (var child in operation.ChildOperations)
        {
            Walk(child, context);
        }
    }

    // ---- handlers ----------------------------------------------------------------------------

    private void OnInvocation(IInvocationOperation invocation, Context context)
    {
        var target = invocation.TargetMethod;
        if (target.MethodKind is MethodKind.LocalFunction or MethodKind.AnonymousFunction)
        {
            return; // the local function's body is walked where it is declared, attributed to this member
        }

        if (invocation.IsImplicit && target.ContainingType?.SpecialType == SpecialType.System_Object)
        {
            return; // implicit ": base()" into System.Object carries no information
        }

        if (_graph.Target(target) is { } targetId)
        {
            var kind = target.MethodKind == MethodKind.Constructor ? "ctor" : "call";
            Emit("CALLS", targetId, invocation.Syntax, context, kind: kind);
        }

        if (NativeBoundary(target) is { } boundary)
        {
            MarkNativeCallbacks(invocation, boundary);
        }

        if (_graph.Rules.MatchLog(target) is { } rule
            && invocation.Arguments.FirstOrDefault(a => a.Parameter?.Ordinal == rule.TemplateArg) is { } message)
        {
            var template = Templates.Build(message.Value, Rules.StyleOf(rule, target));
            EmitMessage(template, "log", Rules.LevelOf(rule, invocation), null, invocation.Syntax, context);
        }

        if (_graph.Rules.MatchConfig(invocation) is { } config)
        {
            EmitConfigRead(config.Key, config.Accessor, invocation.Syntax, context);
        }
    }

    private void OnCreation(IObjectCreationOperation creation, Context context)
    {
        if (creation.Constructor is { } constructor && _graph.Target(constructor) is { } id)
        {
            Emit("CALLS", id, creation.Syntax, context, kind: "ctor");
        }
    }

    private void OnProperty(IPropertyReferenceOperation reference, Context context)
    {
        var property = reference.Property;
        var (read, write) = AccessOf(reference);
        if (_graph.Target(property) is not { } propertyId)
        {
            return;
        }

        if (read)
        {
            if (Accessor(property, getter: true) is { } getter && _graph.Target(getter) is { } getterId)
            {
                Emit("CALLS", getterId, reference.Syntax, context, kind: "getter");
            }

            Emit("READS", propertyId, reference.Syntax, context);
        }

        if (write)
        {
            if (Accessor(property, getter: false) is { } setter && _graph.Target(setter) is { } setterId)
            {
                Emit("CALLS", setterId, reference.Syntax, context, kind: "setter");
            }

            Emit("WRITES", propertyId, reference.Syntax, context);
        }

        if (_graph.Rules.MatchConfig(reference) is { } config)
        {
            EmitConfigRead(config.Key, config.Accessor, reference.Syntax, context);
        }
    }

    private void OnField(IFieldReferenceOperation reference, Context context)
    {
        var field = reference.Field;
        if (field.IsImplicitlyDeclared || _graph.Target(field) is not { } id)
        {
            return; // compiler-generated backing fields have no node
        }

        var (read, write) = AccessOf(reference);
        if (read)
        {
            Emit("READS", id, reference.Syntax, context);
        }

        if (write)
        {
            Emit("WRITES", id, reference.Syntax, context);
        }
    }

    private void EmitWrite(ISymbol member, SyntaxNode syntax, Context context)
    {
        if (!member.IsImplicitlyDeclared && _graph.Target(member) is { } id)
        {
            Emit("WRITES", id, syntax, context, isImplicit: true);
        }
    }

    /// <summary>x.Click += Handler: SUBSCRIBES to the event, with the handler method on the edge.</summary>
    private void OnEventAssignment(IEventAssignmentOperation assignment, Context context)
    {
        if (!assignment.Adds || assignment.EventReference is not IEventReferenceOperation reference
            || _graph.Target(reference.Event) is not { } eventId)
        {
            return;
        }

        var handler = MethodGroup(assignment.HandlerValue) is { } method ? _graph.Target(method) : null;
        var uiEvent = _graph.Rules.IsUiEvent(reference);
        foreach (var owner in context.Owners)
        {
            _graph.Edges.Add(new EdgeRecord
            {
                Type = "SUBSCRIBES",
                Start = owner,
                End = eventId,
                Owner = _graph.AssemblyName,
                File = _file,
                Line = GraphBuilder.Line(assignment.Syntax),
                Via = context.Via,
                Handler = handler,
                UiEvent = uiEvent ? true : null,
            }, discriminator: handler);
        }
    }

    /// <summary>Method group converted to a delegate (Task.Run(Foo), handler fields, callbacks): an indirect call.</summary>
    private void OnMethodReference(IMethodReferenceOperation reference, Context context)
    {
        if (reference.Method.MethodKind == MethodKind.LocalFunction || _graph.Target(reference.Method) is not { } id)
        {
            return;
        }

        _nativeCallbacks.TryGetValue(reference, out var callbackKind);
        foreach (var owner in context.Owners)
        {
            _graph.Edges.Add(new EdgeRecord
            {
                Type = "REFERENCES_METHOD",
                Start = owner,
                End = id,
                Owner = _graph.AssemblyName,
                File = _file,
                Line = GraphBuilder.Line(reference.Syntax),
                Via = context.Via,
                CallbackKind = callbackKind,
            });
        }
    }

    /// <summary>throw new X("text"): EMITS a :Message of kind exception (SPEC 5.3).</summary>
    private void OnThrow(IThrowOperation @throw, Context context)
    {
        if (@throw.Exception is null || Templates.Unwrap(@throw.Exception) is not IObjectCreationOperation { Constructor: not null } creation)
        {
            return;
        }

        if (MessageArgument(creation) is not { } argument)
        {
            return;
        }

        var template = Templates.Build(argument.Value, TemplateStyle.Literal);
        var exceptionType = creation.Type is { } type ? _graph.Target(type) : null;
        EmitMessage(template, "exception", null, exceptionType, @throw.Syntax, context);
    }

    // ---- emit helpers ------------------------------------------------------------------------

    private void Emit(string type, string end, SyntaxNode syntax, Context context, string? kind = null, bool? isImplicit = null)
    {
        var line = GraphBuilder.Line(syntax);
        foreach (var owner in context.Owners)
        {
            _graph.AddEdge(type, owner, end, _file, line, kind: kind, via: context.Via, isImplicit: isImplicit);
        }
    }

    private void EmitMessage(string template, string kind, string? level, string? exceptionType, SyntaxNode syntax, Context context)
    {
        var line = GraphBuilder.Line(syntax);
        foreach (var owner in context.Owners)
        {
            // Same text in two methods = two messages: the emitting method is what a log line should lead to.
            var messageId = Ids.Message(template, owner);
            _graph.Nodes.Add(new NodeRecord
            {
                Label = "Message",
                Id = messageId,
                Name = template.Length > 120 ? template[..120] : template,
                Lang = "cs",
                Assembly = _graph.AssemblyName,
                File = _file,
                LineStart = line,
                LineEnd = line,
                Kind = kind,
                Level = level,
                Template = template,
                TemplateStyle = "dotnet",
            });
            _graph.Edges.Add(new EdgeRecord
            {
                Type = "EMITS",
                Start = owner,
                End = messageId,
                Owner = _graph.AssemblyName,
                File = _file,
                Line = line,
                Via = context.Via,
                ExceptionType = exceptionType,
                Level = level,
            });
        }
    }

    private void EmitConfigRead(string key, string accessor, SyntaxNode syntax, Context context)
    {
        var id = Ids.Config(key);
        _graph.Nodes.Add(new NodeRecord { Label = "ConfigKey", Id = id, Name = key, Fqn = key, Key = key, Accessor = accessor });
        Emit("READS_CONFIG", id, syntax, context);
    }

    // ---- small helpers -----------------------------------------------------------------------

    /// <summary>Read, write or both, from where the reference sits (SPEC 5.3, assignment-target rule).</summary>
    private static (bool Read, bool Write) AccessOf(IOperation reference)
    {
        var parent = reference.Parent;
        switch (parent)
        {
            case ISimpleAssignmentOperation assignment when ReferenceEquals(assignment.Target, reference):
                return (false, true);
            case ICompoundAssignmentOperation compound when ReferenceEquals(compound.Target, reference):
                return (true, true);
            case ICoalesceAssignmentOperation coalesce when ReferenceEquals(coalesce.Target, reference):
                return (true, true);
            case IIncrementOrDecrementOperation increment when ReferenceEquals(increment.Target, reference):
                return (true, true);
            case IArgumentOperation { Parameter.RefKind: RefKind.Out }:
                return (false, true);
            case IArgumentOperation { Parameter.RefKind: RefKind.Ref }:
                return (true, true);
            case IAddressOfOperation:
                return (true, true);
            case ITupleOperation tuple when IsDeconstructionTarget(tuple):
                return (false, true);
            default:
                return (true, false);
        }
    }

    private static bool IsDeconstructionTarget(ITupleOperation tuple)
    {
        IOperation current = tuple;
        while (current.Parent is ITupleOperation outer)
        {
            current = outer;
        }

        return current.Parent is IDeconstructionAssignmentOperation assignment && ReferenceEquals(assignment.Target, current);
    }

    /// <summary>The accessor to call; an override may declare only one, so look at the overridden property too.</summary>
    private static IMethodSymbol? Accessor(IPropertySymbol property, bool getter)
    {
        for (var current = property; current is not null; current = current.OverriddenProperty)
        {
            var accessor = getter ? current.GetMethod : current.SetMethod;
            if (accessor is not null)
            {
                return accessor;
            }
        }

        return null;
    }

    private string? NativeBoundary(IMethodSymbol target)
    {
        if (target.GetDllImportData() is not null)
        {
            return "pinvoke";
        }

        var assembly = target.ContainingAssembly?.Name;
        return assembly is not null && _graph.Settings.CppCliNames.Contains(assembly) ? "cppcli" : null;
    }

    private void MarkNativeCallbacks(IInvocationOperation invocation, string boundary)
    {
        foreach (var argument in invocation.Arguments)
        {
            var value = Templates.Unwrap(argument.Value);
            var reference = value switch
            {
                IDelegateCreationOperation { Target: IMethodReferenceOperation target } => target,
                IMethodReferenceOperation direct => direct,
                _ => null,
            };
            if (reference is not null)
            {
                _nativeCallbacks[reference] = boundary;
            }
        }
    }

    private static IMethodSymbol? MethodGroup(IOperation value) => Templates.Unwrap(value) switch
    {
        IDelegateCreationOperation { Target: IMethodReferenceOperation reference } => reference.Method,
        IMethodReferenceOperation reference => reference.Method,
        _ => null,
    };

    /// <summary>
    /// The message argument of an exception constructor: a string parameter named like "message", else the
    /// first string parameter that is not a parameter name (ArgumentNullException(nameof(x)) has no message).
    /// </summary>
    private static IArgumentOperation? MessageArgument(IObjectCreationOperation creation)
    {
        var strings = creation.Arguments
            .Where(a => a.Parameter?.Type.SpecialType == SpecialType.System_String)
            .ToList();
        return strings.FirstOrDefault(a => IsMessageName(a.Parameter!.Name))
               ?? strings.FirstOrDefault(a => !IsParameterName(a.Parameter!.Name));
    }

    private static bool IsMessageName(string name) =>
        name.Contains("message", StringComparison.OrdinalIgnoreCase)
        || name.Contains("msg", StringComparison.OrdinalIgnoreCase)
        || name.Contains("format", StringComparison.OrdinalIgnoreCase);

    private static bool IsParameterName(string name) =>
        name.Contains("param", StringComparison.OrdinalIgnoreCase) || name.Equals("name", StringComparison.OrdinalIgnoreCase);
}
