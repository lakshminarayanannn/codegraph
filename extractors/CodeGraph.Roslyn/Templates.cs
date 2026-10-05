using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.Operations;

namespace CodeGraph.Roslyn;

internal enum TemplateStyle
{
    Literal,    // the text is printed as is: braces are literal characters
    Composite,  // the text is a .NET composite format string: "{0}" is a placeholder
}

/// <summary>
/// Turns the message argument of a log call or exception into a template (SPEC 5.4).
/// The result is always a .NET composite format string: constant text is kept, every non-constant
/// part becomes a "{N}" hole, and literal braces are doubled. Fully dynamic text gives "&lt;dynamic&gt;".
/// </summary>
internal static class Templates
{
    public const string Dynamic = "<dynamic>";

    public static string Build(IOperation value, TemplateStyle style)
    {
        var builder = new Builder();
        builder.Append(value, style);
        return builder.HasLiteralText ? builder.ToString() : Dynamic;
    }

    /// <summary>Strips implicit and explicit conversions: log4net's Error(object) receives (object)"text".</summary>
    public static IOperation Unwrap(IOperation operation)
    {
        while (operation is IConversionOperation conversion)
        {
            operation = conversion.Operand;
        }

        return operation;
    }

    private sealed class Builder
    {
        private readonly StringBuilder _text = new();
        private int _holes;

        public bool HasLiteralText { get; private set; }

        public void Append(IOperation operation, TemplateStyle style)
        {
            operation = Unwrap(operation);

            // Literals, const fields, nameof(...) and constant-folded concatenations all carry a constant value.
            if (operation.ConstantValue is { HasValue: true, Value: string constant })
            {
                AppendText(constant, style);
                return;
            }

            switch (operation)
            {
                case IInterpolatedStringOperation interpolated:
                    foreach (var part in interpolated.Parts)
                    {
                        if (part is IInterpolatedStringTextOperation { Text.ConstantValue.Value: string text })
                        {
                            AppendText(text, TemplateStyle.Literal); // "{{" was already unescaped by the compiler
                        }
                        else if (part is IInterpolationOperation { Expression.ConstantValue: { HasValue: true, Value: string inline } })
                        {
                            AppendText(inline, TemplateStyle.Literal);
                        }
                        else
                        {
                            AppendHole();
                        }
                    }

                    return;

                case IBinaryOperation { OperatorKind: BinaryOperatorKind.Add } add
                    when add.Type?.SpecialType == SpecialType.System_String:
                    Append(add.LeftOperand, style);
                    Append(add.RightOperand, style);
                    return;

                case IInvocationOperation call when IsString(call, "Format") && Argument(call, "format") is { } format:
                    Append(format.Value, TemplateStyle.Composite); // string.Format("...{0}...", x): the format is the template
                    return;

                case IInvocationOperation call when IsString(call, "Concat"):
                    foreach (var argument in call.Arguments)
                    {
                        AppendArgument(argument, style);
                    }

                    return;
            }

            AppendHole();
        }

        public override string ToString() => _text.ToString();

        private void AppendArgument(IArgumentOperation argument, TemplateStyle style)
        {
            // string.Concat(params object[]) wraps the parts in an implicit array.
            if (argument.ArgumentKind == ArgumentKind.ParamArray && argument.Value is IArrayCreationOperation { Initializer: { } init })
            {
                foreach (var element in init.ElementValues)
                {
                    Append(element, style);
                }
            }
            else
            {
                Append(argument.Value, style);
            }
        }

        private void AppendText(string text, TemplateStyle style)
        {
            if (style == TemplateStyle.Composite)
            {
                _text.Append(text); // already a composite format string
                HasLiteralText |= HasLiteralCharacters(text);
            }
            else
            {
                _text.Append(text.Replace("{", "{{").Replace("}", "}}"));
                HasLiteralText |= text.Length > 0;
            }
        }

        private void AppendHole() => _text.Append('{').Append(_holes++).Append('}');

        /// <summary>True when a composite format string has text outside its {placeholders}.</summary>
        private static bool HasLiteralCharacters(string format)
        {
            var depth = 0;
            for (var i = 0; i < format.Length; i++)
            {
                var c = format[i];
                if (c == '{' && i + 1 < format.Length && format[i + 1] == '{')
                {
                    return true; // escaped brace is a literal character
                }

                if (c == '{')
                {
                    depth++;
                }
                else if (c == '}' && depth > 0)
                {
                    depth--;
                }
                else if (depth == 0)
                {
                    return true;
                }
            }

            return false;
        }

        private static bool IsString(IInvocationOperation call, string name) =>
            call.TargetMethod.Name == name && call.TargetMethod.ContainingType?.SpecialType == SpecialType.System_String;

        private static IArgumentOperation? Argument(IInvocationOperation call, string parameterName) =>
            call.Arguments.FirstOrDefault(a => a.Parameter?.Name == parameterName);
    }
}
