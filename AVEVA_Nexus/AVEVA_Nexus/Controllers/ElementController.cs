using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Services;
using Newtonsoft.Json;

namespace AVEVA_Nexus.Controllers
{
    public class ElementController
    {
        private readonly ElementService _service;
        // A new QueryService (and so, by default, a new provider) per query, as before the provider seam.
        private readonly Func<QueryService> _queryService;

        public ElementController() : this(new ElementService(), () => new QueryService()) { }

        public ElementController(IAvevaProvider provider)
            : this(new ElementService(provider), () => new QueryService(provider)) { }

        private ElementController(ElementService service, Func<QueryService> queryService)
        {
            _service = service;
            _queryService = queryService;
        }

        public ApiResponse Query(string json)
        {
            QueryRequest request = Http.JsonBody.Parse<QueryRequest>(json);

            return new ApiResponse
            {
                Success = true,
                Data = _queryService().Run(request)
            };
        }

        public ApiResponse Get(string json)
        {
            GetRequest request = Http.JsonBody.Parse<GetRequest>(json);

            return new ApiResponse { Success = true, Data = _service.Get(request) };
        }

        public ApiResponse Children(string dbref, string type, string limit)
        {
            return new ApiResponse { Success = true, Data = _service.Children(dbref, type, limit) };
        }

        public ApiResponse Resolve(string json)
        {
            return new ApiResponse { Success = true, Data = _service.Resolve(Http.JsonBody.Parse<ResolveRequest>(json)) };
        }

        public ApiResponse Tree(string dbref, string depth, string type, string limit)
        {
            return new ApiResponse { Success = true, Data = _service.Tree(dbref, depth, type, limit) };
        }

        public ApiResponse AttributeList(string type)
        {
            return new ApiResponse { Success = true, Data = _service.AttributeList(type) };
        }

        public ApiResponse GetAttribute(
            string dbref,
            string attribute)
        {
            return new ApiResponse
            {
                Success = true,
                Data = _service.GetAttribute(
                    dbref,
                    attribute)
            };
        }
    }
}
