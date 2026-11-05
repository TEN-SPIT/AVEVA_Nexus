using System.Collections.Generic;
using Newtonsoft.Json.Linq;

namespace AVEVA_Nexus.Models
{
    // Body of POST /api/Batch
    public class BatchRequest
    {
        public List<BatchSubRequest> Requests { get; set; }
    }

    public class BatchSubRequest
    {
        public string Method { get; set; }
        public string Path { get; set; }
        public Dictionary<string, JToken> Query { get; set; }
        public JToken Body { get; set; }
    }

    // One entry of the Data array of POST /api/Batch, in the order of the requests.
    public class BatchItemResult
    {
        public int Status { get; set; }
        public object Data { get; set; }
        public string Error { get; set; }
    }
}
