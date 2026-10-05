using System.Reflection;
using log4net;

namespace MiniPlant.Core
{
    /// <summary>Overrides the base Calibrate; moves through a [DllImport] into the native DLL.</summary>
    public class LinearStage : StageBase
    {
        private static readonly ILog Log = LogManager.GetLogger(typeof(LinearStage));

        public LinearStage(string name) : base(name)
        {
            // Method group passed to a DllImport method: OnNativePosition is a native callback entry point.
            NativeMethods.SetPositionCallback(OnNativePosition);
        }

        public override void Calibrate()
        {
            base.Calibrate();
            Log.Warn("Calibration of " + Name + " used the limit switch");
        }

        public void RunSelfTest()
        {
            // Reached only through reflection: a known false positive in unreferenced_symbols.
            GetType().GetMethod("RunDiagnostics", BindingFlags.NonPublic | BindingFlags.Instance).Invoke(this, null);
        }

        protected override bool DoMove(double targetMm)
        {
            return NativeMethods.MiniPlant_Stage_Move(targetMm) == 0;
        }

        private void RunDiagnostics()
        {
            Log.Info("Diagnostics passed");
        }

        // Never referenced: expected in unreferenced_symbols.
        private void LegacyRecalibrate()
        {
            Calibrate();
        }

        private static void OnNativePosition(double positionMm)
        {
            Log.Debug($"Native position {positionMm}");
        }
    }

    /// <summary>Override of an override.</summary>
    public class PrecisionLinearStage : LinearStage
    {
        private static readonly ILog Log = LogManager.GetLogger(typeof(PrecisionLinearStage));

        private double _offsetUm;

        public PrecisionLinearStage(string name) : base(name)
        {
        }

        public override void Calibrate()
        {
            base.Calibrate();
            _offsetUm = 0.5;
            Log.InfoFormat("Precision offset {0:F3} um", _offsetUm);
        }
    }
}
