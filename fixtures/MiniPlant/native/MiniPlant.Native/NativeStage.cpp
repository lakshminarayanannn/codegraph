#include "NativeStage.h"

namespace MiniPlant
{
namespace Native
{
static PositionCallback g_callback = nullptr;

double NativeStageBase::Position() const
{
    return m_position;
}

NativeStage::NativeStage()
{
    m_position = 0.0;
}

bool NativeStage::CheckLimits(double targetMm) const
{
    return targetMm >= -100.0 && targetMm <= 100.0;
}

int NativeStage::Move(double targetMm)
{
    if (!CheckLimits(targetMm))
    {
        MP_LOG_ERROR("Target %.3f mm is outside the travel range", targetMm);
        return -1;
    }

    m_position = targetMm;
    if (g_callback != nullptr)
    {
        g_callback(m_position);
    }
    return 0;
}

double NativeStage::Position() const
{
    return NativeStageBase::Position();
}

void NativeStage::Home()
{
    // Virtual call through a base pointer: the static target is NativeStageBase::Move.
    NativeStageBase* self = this;
    self->Move(0.0);
}

void SetPositionCallback(PositionCallback callback)
{
    g_callback = callback;
}
}
}
