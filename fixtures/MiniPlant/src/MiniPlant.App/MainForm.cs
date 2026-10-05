using System;
using System.Windows.Forms;
using MiniPlant.Services;

namespace MiniPlant.App
{
    /// <summary>UI event handler: OnStartClicked is subscribed to Button.Click.</summary>
    public class MainForm : Form
    {
        private readonly Button _startButton = new Button();
        private readonly InspectionService _service;

        public MainForm(InspectionService service, string plantName)
        {
            _service = service;
            Text = plantName;
            _startButton.Click += OnStartClicked;
            Controls.Add(_startButton);
        }

        private void OnStartClicked(object sender, EventArgs e)
        {
            _service.Start();
        }
    }
}
