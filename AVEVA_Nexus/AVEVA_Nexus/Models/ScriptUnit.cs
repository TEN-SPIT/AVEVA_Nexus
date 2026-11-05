using System.Collections.Generic;

namespace AVEVA_Nexus.Models
{
    // One thing PML/Run executes: a single command line, or a complete if/do/handle block that must run as a whole.
    // Line numbers are 1-based positions in the original script.
    public class ScriptUnit
    {
        public int FirstLine { get; set; }
        public int LastLine { get; set; }
        public bool IsBlock { get; set; }
        public List<string> Lines { get; set; } = new List<string>();
    }
}
