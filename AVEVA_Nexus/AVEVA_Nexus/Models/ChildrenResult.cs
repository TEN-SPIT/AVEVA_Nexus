using System.Collections.Generic;

namespace AVEVA_Nexus.Models
{
    // Data of GET /api/Element/Children. Each child has the keys "dbref", "type" and "name".
    // Truncated is true when the element has more children than the limit.
    public class ChildrenResult
    {
        public string Owner { get; set; }
        public List<Dictionary<string, object>> Children { get; set; } = new List<Dictionary<string, object>>();
        public bool Truncated { get; set; }
    }
}
