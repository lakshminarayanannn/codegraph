using System.Collections.Generic;
using System.Configuration;
using log4net;
using MiniPlant.Core;
using MiniPlant.UI.Controls;

namespace MiniPlant.Services
{
    /// <summary>Event subscription, config read, field read/write and a layer violation.</summary>
    public class InspectionService
    {
        private static readonly ILog Log = LogManager.GetLogger(typeof(InspectionService));

        private readonly IStage _stage;
        private readonly MotionController _motion;
        private int _inspectionCount;

        public InspectionService(IStage stage)
        {
            _stage = stage;
            _motion = new MotionController(stage);
        }

        public int InspectionCount => _inspectionCount;

        public void Start()
        {
            if (_stage is StageBase stageBase)
            {
                stageBase.PositionChanged += OnStagePositionChanged;
            }

            string maxSpeed = ConfigurationManager.AppSettings["Stage.MaxSpeed"];
            Log.Info("Inspection service started, max speed " + maxSpeed);
        }

        public void Inspect(Recipe recipe)
        {
            recipe.Validate();
            _motion.MoveThrough(ToArray(recipe.Points));
            _inspectionCount++;

            // Layer violation: a services-layer class creates a ui-layer control.
            new StatusPanel().ShowMessage("Inspected " + recipe.Name);
        }

        private static double[] ToArray(IReadOnlyList<double> points)
        {
            var result = new double[points.Count];
            for (int i = 0; i < result.Length; i++)
            {
                result[i] = points[i];
            }

            return result;
        }

        private void OnStagePositionChanged(object sender, PositionChangedEventArgs e)
        {
            Log.DebugFormat("Stage moved to {0}", e.Position);
        }
    }
}
