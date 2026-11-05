using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Services;

namespace AVEVA_Nexus.Controllers
{
    public class SessionController
    {
        private readonly SessionService _service;

        public SessionController() : this(new SessionService()) { }

        public SessionController(IAvevaProvider provider) : this(new SessionService(provider)) { }

        private SessionController(SessionService service)
        {
            _service = service;
        }

        public ApiResponse Info()
        {
            return new ApiResponse { Success = true, Data = _service.Info() };
        }
    }
}
