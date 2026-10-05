using System.Runtime.InteropServices;

namespace MiniPlant.Core
{
    /// <summary>P/Invoke declarations into the native DLL (native/MiniPlant.Native).</summary>
    internal static class NativeMethods
    {
        [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
        internal delegate void PositionCallback(double positionMm);

        [DllImport("MiniPlant.Native.dll", CallingConvention = CallingConvention.Cdecl)]
        internal static extern int MiniPlant_Stage_Move(double targetMm);

        [DllImport("MiniPlant.Native.dll", EntryPoint = "MiniPlant_SetPositionCallback", CallingConvention = CallingConvention.Cdecl)]
        internal static extern void SetPositionCallback(PositionCallback callback);
    }
}
