using System.Threading.Tasks;
using log4net;

namespace MiniPlant.Core
{
    /// <summary>Calls through the IStage interface, from a lambda and from a local function.</summary>
    public class MotionController
    {
        private static readonly ILog Log = LogManager.GetLogger(typeof(MotionController));

        private readonly IStage _stage;

        public MotionController(IStage stage)
        {
            _stage = stage;
        }

        public void MoveTo(double targetMm)
        {
            _stage.Move(targetMm);
        }

        public Task MoveToAsync(double targetMm)
        {
            return Task.Run(() => _stage.Move(targetMm));
        }

        public void MoveThrough(double[] points)
        {
            foreach (var point in points)
            {
                Step(point);
            }

            void Step(double p)
            {
                _stage.Move(p);
                Log.Debug("Step done");
            }
        }
    }
}
