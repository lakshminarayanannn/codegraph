using System;
using System.Configuration;
using System.Windows.Forms;
using MiniPlant.Core;
using MiniPlant.Services;

namespace MiniPlant.App
{
    /// <summary>Main entry point: generic calls with two type arguments and an extension call.</summary>
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            var registry = new Registry();
            var stage = new PrecisionLinearStage("X");
            registry.Add<IStage>(stage);
            registry.Add(new MotionController(stage));

            var service = new InspectionService(registry.Get<IStage>());
            registry.Get<MotionController>().MoveTo(1.0);
            stage.MoveHome();

            string plantName = ConfigurationManager.AppSettings.Get("Plant.Name");
            Application.Run(new MainForm(service, plantName));
        }
    }
}
