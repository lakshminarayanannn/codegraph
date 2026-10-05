#include "StageWrapper.h"

using namespace MiniPlant::Interop;

StageWrapper::StageWrapper()
    : m_native(new MiniPlant::Native::NativeStage())
{
}

StageWrapper::~StageWrapper()
{
    this->!StageWrapper();
}

StageWrapper::!StageWrapper()
{
    delete m_native;
    m_native = nullptr;
}

void StageWrapper::Move(double targetMm)
{
    // Virtual native call: compiled to calli through the vtable (no method token in IL).
    int rc = m_native->Move(targetMm);
    if (rc != 0)
    {
        throw gcnew System::InvalidOperationException("Native move failed");
    }
}

double StageWrapper::GetPosition()
{
    return m_native->Position();
}

void StageWrapper::Home()
{
    // Non-virtual native call: compiled to a call of a pinvokeimpl method in <Module>.
    m_native->Home();
}

unsigned int StageWrapper::Uptime()
{
    // Imported Win32 function: a real P/Invoke with an ImplMap row.
    return ::GetTickCount();
}
