using System;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using MiniPlant.Native;

namespace MiniPlant.Native
{
    // IJW represents a native class in metadata as an opaque value type marked [NativeCppClass].
    [NativeCppClass]
    [StructLayout(LayoutKind.Sequential, Size = 16)]
    internal struct NativeStage
    {
    }
}

namespace MiniPlant.Interop
{
    // Marks a P/Invoke stub that SimIjwRewriter converts into a C++/CLI-style IJW stub:
    // a pinvokeimpl method with no ImplMap row ("No map"), moved to <Module>, renamed to the given name.
    [AttributeUsage(AttributeTargets.Method)]
    internal sealed class SimIjwAttribute : Attribute
    {
        public SimIjwAttribute(string metadataName)
        {
            MetadataName = metadataName;
        }

        public string MetadataName { get; }
    }

    /// <summary>C# emulation of the C++/CLI ref class in interop/MiniPlant.Interop/StageWrapper.cpp.</summary>
    public unsafe class StageWrapper : IDisposable
    {
        private NativeStage* m_native;

        public StageWrapper()
        {
            m_native = (NativeStage*)Marshal.AllocHGlobal(sizeof(NativeStage));
            NativeStage_Ctor(m_native);
        }

        ~StageWrapper()
        {
            Release();
        }

        public void Dispose()
        {
            Release();
            GC.SuppressFinalize(this);
        }

        public void Move(double targetMm)
        {
            // Virtual native call: C++/CLI emits calli through the vtable, so no method token exists.
            IntPtr* vtable = *(IntPtr**)m_native;
            int rc = ((delegate* unmanaged[Cdecl]<NativeStage*, double, int>)vtable[1])(m_native, targetMm);
            if (rc != 0)
            {
                throw new InvalidOperationException("Native move failed");
            }
        }

        public double GetPosition()
        {
            IntPtr* vtable = *(IntPtr**)m_native;
            return ((delegate* unmanaged[Cdecl]<NativeStage*, double>)vtable[2])(m_native);
        }

        public void Home()
        {
            NativeStage_Home(m_native);
        }

        public uint Uptime()
        {
            // A real P/Invoke (ImplMap row present), as C++/CLI emits for imported Win32 functions.
            return GetTickCount();
        }

        private void Release()
        {
            if (m_native != null)
            {
                IntPtr* vtable = *(IntPtr**)m_native;
                ((delegate* unmanaged[Cdecl]<NativeStage*, uint, void*>)vtable[0])(m_native, 1);
                m_native = null;
            }
        }

        [SimIjw("MiniPlant.Native.NativeStage.{ctor}")]
        [DllImport("MiniPlant.Native.dll")]
        private static extern void NativeStage_Ctor(NativeStage* self);

        [SimIjw("MiniPlant.Native.NativeStage.Home")]
        [DllImport("MiniPlant.Native.dll")]
        private static extern void NativeStage_Home(NativeStage* self);

        [DllImport("kernel32.dll")]
        private static extern uint GetTickCount();
    }
}
