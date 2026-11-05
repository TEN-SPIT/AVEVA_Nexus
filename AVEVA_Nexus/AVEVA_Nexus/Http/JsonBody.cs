using AVEVA_Nexus.Exceptions;
using Newtonsoft.Json;

namespace AVEVA_Nexus.Http
{
    // Request body parsing. A body that is not valid JSON (or does not fit the expected shape) is the client's
    // fault: 400 with Newtonsoft's description (position included), never a 500.
    // Pure logic: no Aveva.* references.
    public static class JsonBody
    {
        public static T Parse<T>(string json) where T : class
        {
            if (string.IsNullOrWhiteSpace(json)) return null;
            try
            {
                return JsonConvert.DeserializeObject<T>(json);
            }
            catch (JsonException ex)
            {
                throw new NexusException("Malformed JSON body: " + ex.Message, 400);
            }
        }
    }
}
