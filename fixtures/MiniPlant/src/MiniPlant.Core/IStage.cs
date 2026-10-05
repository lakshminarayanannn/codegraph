namespace MiniPlant.Core
{
    /// <summary>A motion axis. Implemented by the StageBase family and by PiezoStage.</summary>
    public interface IStage
    {
        string Name { get; }

        void Move(double targetMm);

        double ReadPosition();
    }
}
