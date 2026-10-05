#pragma once
#include "Logging.h"

#ifdef MINIPLANT_NATIVE_EXPORTS
#define MP_API __declspec(dllexport)
#else
#define MP_API __declspec(dllimport)
#endif

namespace MiniPlant
{
namespace Native
{
typedef void (*PositionCallback)(double positionMm);

// Class hierarchy with a pure virtual method and an override.
class MP_API NativeStageBase
{
public:
    virtual ~NativeStageBase() {}
    virtual int Move(double targetMm) = 0;
    virtual double Position() const;

protected:
    double m_position = 0.0;
};

class MP_API NativeStage : public NativeStageBase
{
public:
    NativeStage();
    int Move(double targetMm) override;
    double Position() const override;
    void Home();

private:
    bool CheckLimits(double targetMm) const;
};

void SetPositionCallback(PositionCallback callback);
}
}

// C exports used by [DllImport] in MiniPlant.Core.
extern "C" MP_API int MiniPlant_Stage_Move(double targetMm);
extern "C" MP_API void MiniPlant_SetPositionCallback(MiniPlant::Native::PositionCallback callback);
