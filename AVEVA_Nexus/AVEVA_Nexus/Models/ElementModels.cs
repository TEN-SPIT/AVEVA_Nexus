using System.Collections.Generic;

namespace AVEVA_Nexus.Models
{
    // Body of POST /api/Element/Resolve
    public class ResolveRequest
    {
        public List<string> Names { get; set; }
    }

    // One entry of POST /api/Element/Resolve. Dbref and Type are null when the element was not found.
    public class ResolvedElement
    {
        public string Name { get; set; }
        public string Dbref { get; set; }
        public string Type { get; set; }
    }

    public class TreeNode
    {
        public string Dbref { get; set; }
        public string Type { get; set; }
        public string Name { get; set; }
    }

    public class TreeItem
    {
        public string Dbref { get; set; }
        public string Type { get; set; }
        public string Name { get; set; }
        public int Depth { get; set; }
        public string Owner { get; set; }
    }

    // Data of GET /api/Element/Tree
    public class TreeResult
    {
        public TreeNode Root { get; set; }
        public List<TreeItem> Items { get; set; } = new List<TreeItem>();
        public bool Truncated { get; set; }
    }
}
