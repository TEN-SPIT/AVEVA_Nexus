using System;
using System.Collections.Generic;

namespace AVEVA_Nexus.Query
{
    // Maps what a client typed as an element type (full name "EQUIPMENT" or short name "EQUI", any case) to the
    // full type name. Built from the (Name, ShortName) pairs of DbElementType.GetAllElementTypes(), so nothing
    // unvalidated ever reaches DbElementType.GetElementType (an unknown name crashes E3D). A full name always
    // wins over a short name; when several types share a short name, the first one listed wins.
    // Pure logic: no Aveva.* references.
    public class TypeNameMap
    {
        readonly Dictionary<string, string> _byName = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        readonly Dictionary<string, string> _byShort = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        public TypeNameMap(IEnumerable<KeyValuePair<string, string>> nameAndShortName)
        {
            foreach (var pair in nameAndShortName)
            {
                if (string.IsNullOrWhiteSpace(pair.Key)) continue;
                if (!_byName.ContainsKey(pair.Key)) _byName[pair.Key] = pair.Key;
                if (!string.IsNullOrWhiteSpace(pair.Value) && !_byShort.ContainsKey(pair.Value)) _byShort[pair.Value] = pair.Key;
            }
        }

        public int Count { get { return _byName.Count; } }

        // Full type name, or null if the text is not a known type.
        public string Canonical(string type)
        {
            if (string.IsNullOrWhiteSpace(type)) return null;
            string t = type.Trim();
            string name;
            if (_byName.TryGetValue(t, out name)) return name;
            if (_byShort.TryGetValue(t, out name)) return name;
            return null;
        }
    }
}
