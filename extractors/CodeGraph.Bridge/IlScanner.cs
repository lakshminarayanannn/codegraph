using System.Reflection;
using System.Reflection.Emit;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;

namespace CodeGraph.Bridge;

/// <summary>One IL instruction that references another member: the opcode, its token and its byte offset.</summary>
internal readonly record struct IlReference(ILOpCode OpCode, int Token, int Offset);

/// <summary>
/// Minimal IL decoder: walks a method body and yields the instructions whose operand is a metadata token
/// (call, callvirt, newobj, ldftn, ldvirtftn, calli, field loads and stores). Operand sizes come from
/// System.Reflection.Emit.OpCodes, the runtime's own opcode table, so no hand-written table can drift.
/// </summary>
internal static class IlScanner
{
    private static readonly Dictionary<short, OperandType> Operands = typeof(OpCodes)
        .GetFields(BindingFlags.Public | BindingFlags.Static)
        .Select(f => (OpCode)f.GetValue(null)!)
        .ToDictionary(o => o.Value, o => o.OperandType);

    private static readonly HashSet<ILOpCode> Interesting = new()
    {
        ILOpCode.Call, ILOpCode.Callvirt, ILOpCode.Newobj, ILOpCode.Ldftn, ILOpCode.Ldvirtftn, ILOpCode.Calli,
        ILOpCode.Ldfld, ILOpCode.Ldflda, ILOpCode.Stfld, ILOpCode.Ldsfld, ILOpCode.Ldsflda, ILOpCode.Stsfld,
    };

    public static IEnumerable<IlReference> References(MethodBodyBlock body)
    {
        var reader = body.GetILReader();
        while (reader.RemainingBytes > 0)
        {
            var offset = reader.Offset;
            // Two-byte opcodes start with 0xFE; their table value is (0xFE << 8) | second byte.
            int first = reader.ReadByte();
            var value = first == 0xFE ? (short)(0xFE00 | reader.ReadByte()) : (short)first;
            var opCode = (ILOpCode)(ushort)value;
            if (!Operands.TryGetValue(value, out var operand))
            {
                yield break; // unknown opcode: the body is not plain IL; stop rather than misread it
            }

            if (Interesting.Contains(opCode))
            {
                yield return new IlReference(opCode, reader.ReadInt32(), offset);
                continue;
            }

            Skip(ref reader, operand);
        }
    }

    private static void Skip(ref BlobReader reader, OperandType operand)
    {
        switch (operand)
        {
            case OperandType.InlineNone:
                break;
            case OperandType.ShortInlineBrTarget or OperandType.ShortInlineI or OperandType.ShortInlineVar:
                reader.Offset += 1;
                break;
            case OperandType.InlineVar:
                reader.Offset += 2;
                break;
            case OperandType.InlineI8 or OperandType.InlineR:
                reader.Offset += 8;
                break;
            case OperandType.InlineSwitch:
                var targets = reader.ReadInt32();
                reader.Offset += 4 * targets;
                break;
            default: // every other operand (tokens, 32-bit immediates, branch targets, ShortInlineR) is 4 bytes
                reader.Offset += 4;
                break;
        }
    }

    /// <summary>calli through a native function pointer (a virtual native call from C++/CLI) vs a managed calli.</summary>
    public static bool IsUnmanagedCalli(MetadataReader metadata, int token)
    {
        var handle = (StandaloneSignatureHandle)MetadataTokens.EntityHandle(token);
        var header = metadata.GetBlobReader(metadata.GetStandaloneSignature(handle).Signature).ReadSignatureHeader();
        return header.CallingConvention is not (SignatureCallingConvention.Default or SignatureCallingConvention.VarArgs);
    }
}
