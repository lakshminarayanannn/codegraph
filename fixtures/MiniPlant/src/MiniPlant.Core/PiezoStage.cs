using MiniPlant.Interop;

namespace MiniPlant.Core
{
    /// <summary>Second IStage implementation; delegates to the C++/CLI wrapper.</summary>
    public class PiezoStage : IStage
    {
        private readonly StageWrapper _wrapper = new StageWrapper();

        public string Name => "piezo";

        public void Move(double targetMm)
        {
            _wrapper.Move(targetMm);
        }

        public double ReadPosition() => _wrapper.GetPosition();

        public void Home()
        {
            _wrapper.Home();
        }
    }
}
