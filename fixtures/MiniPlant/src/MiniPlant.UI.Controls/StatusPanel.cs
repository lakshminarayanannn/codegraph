using System.Windows.Forms;

namespace MiniPlant.UI.Controls
{
    /// <summary>A ui-layer control (the services layer uses it: a deliberate layer violation).</summary>
    public class StatusPanel : UserControl
    {
        private readonly Label _label = new Label();

        public StatusPanel()
        {
            Controls.Add(_label);
        }

        public void ShowMessage(string text)
        {
            _label.Text = text;
        }
    }
}
