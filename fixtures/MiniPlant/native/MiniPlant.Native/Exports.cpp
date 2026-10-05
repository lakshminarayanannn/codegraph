#include "NativeStage.h"

namespace
{
MiniPlant::Native::NativeStage g_stage;
}

extern "C" int MiniPlant_Stage_Move(double targetMm)
{
    return g_stage.Move(targetMm);
}

extern "C" void MiniPlant_SetPositionCallback(MiniPlant::Native::PositionCallback callback)
{
    MiniPlant::Native::SetPositionCallback(callback);
}
