using System.ServiceModel;
using MiniPlant.Core;

namespace MiniPlant.Services
{
    /// <summary>WCF contract: [OperationContract] marks a remote entry point.</summary>
    [ServiceContract]
    public interface IInspectionRemote
    {
        [OperationContract]
        void TriggerInspection(string recipeName);
    }

    public class InspectionRemote : IInspectionRemote
    {
        private readonly InspectionService _service;

        public InspectionRemote(InspectionService service)
        {
            _service = service;
        }

        public void TriggerInspection(string recipeName)
        {
            _service.Inspect(new Recipe(recipeName));
        }
    }
}
