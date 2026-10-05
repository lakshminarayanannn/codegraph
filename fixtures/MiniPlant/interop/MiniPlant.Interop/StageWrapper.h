#pragma once
#include <windows.h>
#include "NativeStage.h"

namespace MiniPlant
{
namespace Interop
{
// C++/CLI ref class wrapping a native class (component C: WRAPS and CALLS_NATIVE).
public ref class StageWrapper
{
public:
    StageWrapper();
    ~StageWrapper();
    !StageWrapper();

    void Move(double targetMm);
    double GetPosition();
    void Home();
    unsigned int Uptime();

private:
    MiniPlant::Native::NativeStage* m_native;
};
}
}
