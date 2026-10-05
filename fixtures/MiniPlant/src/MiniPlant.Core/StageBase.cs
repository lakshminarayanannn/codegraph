using System;
using log4net;

namespace MiniPlant.Core
{
    /// <summary>Abstract base: virtual method chain Calibrate (base -> override -> override of override).</summary>
    public abstract class StageBase : IStage
    {
        public const string MoveFailedMessage = "Stage move failed";

        private static readonly ILog Log = LogManager.GetLogger(typeof(StageBase));

        protected double position;

        protected StageBase(string name)
        {
            Name = name;
        }

        public string Name { get; }

        public event EventHandler<PositionChangedEventArgs> PositionChanged;

        public void Move(double targetMm)
        {
            if (double.IsNaN(targetMm))
            {
                throw new InvalidOperationException("Target position is not a number");
            }

            Log.Info($"Moving {Name} to {targetMm} mm");
            if (!DoMove(targetMm))
            {
                Log.Error(MoveFailedMessage);
                return;
            }

            position = targetMm;
            OnPositionChanged(targetMm);
        }

        public double ReadPosition() => position;

        public virtual void Calibrate()
        {
            Log.Debug(string.Format("Calibrating {0} at {1:F2} mm", Name, position));
        }

        protected abstract bool DoMove(double targetMm);

        protected virtual void OnPositionChanged(double newPosition)
        {
            PositionChanged?.Invoke(this, new PositionChangedEventArgs(newPosition));
        }
    }

    public sealed class PositionChangedEventArgs : EventArgs
    {
        public PositionChangedEventArgs(double position)
        {
            Position = position;
        }

        public double Position { get; }
    }
}
