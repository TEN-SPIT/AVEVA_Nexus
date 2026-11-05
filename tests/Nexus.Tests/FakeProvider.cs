using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Query;

namespace Nexus.Tests
{
    // In-memory E3D model implementing IAvevaProvider deterministically, with the same error contract as
    // AvevaProvider (messages and status codes copied from it):
    //   unknown type -> NexusException "Unknown element type 'X'" (400)
    //   missing element in Children/Owner/Tree -> "Element =1/2 not found" (404)
    //   unknown query scope -> "Scope 'X' not found" (404); bad where -> "PML collect failed ..." (400)
    //   undefined variable -> VariableNotFoundException (404); failing command -> "PML command failed: ..." (400)
    //   ReadAttributes: unknown attribute or invalid element -> null value
    //   GetAttribute: unknown element -> 404, unknown attribute -> 400 (AvevaProvider checks both)
    //
    // Builder:  var m = new FakeProvider();               // has WORLD =0/1
    //           var site = m.Add(m.World, "SITE", "/S1");  // dbref =1/1, =1/2, ... in creation order
    //           m.Add(site, "ZONE", "/Z1", "DESC", "zone one");   // extra attributes as name/value pairs
    public sealed class FakeProvider : IAvevaProvider
    {
        public sealed class Element
        {
            public string Dbref;
            public string Type;
            public string Name;
            public Element Owner;
            public readonly List<Element> Members = new List<Element>();
            public readonly Dictionary<string, object> Attributes =
                new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
        }

        readonly Dictionary<string, Element> _byRef = new Dictionary<string, Element>();
        readonly List<KeyValuePair<string, string>> _types = new List<KeyValuePair<string, string>>();
        readonly Dictionary<string, string[]> _udas = new Dictionary<string, string[]>(StringComparer.OrdinalIgnoreCase);
        TypeNameMap _typeMap;
        int _next = 1;

        static readonly string[] SystemAttributes = { "NAME", "TYPE", "REF", "OWNER", "DESC" };

        public Element World { get; }

        // Globals visible to the PML variable getters and RunScript's return values (names without "!!", any case).
        public Dictionary<string, object> Globals { get; } = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);

        // Lines for which this returns true fail (ExecuteCommand throws, RunScript records an error).
        public Func<string, bool> FailingLine { get; set; } = line => line.IndexOf("FAIL", StringComparison.Ordinal) >= 0;

        public List<string> Executed { get; } = new List<string>();
        public List<ScriptUnit> RunUnits { get; } = new List<ScriptUnit>();
        public int ReadAttributesCalls { get; private set; }

        public SessionInfo Session { get; set; } = new SessionInfo
        {
            Project = "TST", Mdb = "/TST-MDB", User = "SYSTEM", Module = "Design", E3dVersion = "3.1.0"
        };

        public FakeProvider()
        {
            DefineType("WORLD", "WORL");
            DefineType("SITE", "SITE");
            DefineType("ZONE", "ZONE");
            DefineType("EQUIPMENT", "EQUI", ":TP-MTO-AREA");
            DefineType("PIPE", "PIPE");
            DefineType("BRANCH", "BRAN");
            World = new Element { Dbref = "=0/1", Type = "WORLD", Name = "/*" };
            _byRef[World.Dbref] = World;
        }

        public FakeProvider DefineType(string name, string shortName, params string[] udas)
        {
            _types.Add(new KeyValuePair<string, string>(name, shortName));
            _udas[name] = udas;
            _typeMap = null;
            return this;
        }

        // attributes: name, value, name, value...
        public Element Add(Element owner, string type, string name = null, params object[] attributes)
        {
            if (Canonical(type) == null) throw new ArgumentException("type not defined in the fake: " + type);
            var element = new Element { Dbref = "=1/" + _next++, Type = Canonical(type), Name = name, Owner = owner };
            for (int i = 0; i + 1 < attributes.Length; i += 2)
            {
                element.Attributes[(string)attributes[i]] = attributes[i + 1];
            }
            owner.Members.Add(element);
            _byRef[element.Dbref] = element;
            return element;
        }

        // ---- helpers mirroring AvevaProvider ------------------------------------------------------------------

        string Canonical(string type)
        {
            if (_typeMap == null) _typeMap = new TypeNameMap(_types);
            return _typeMap.Canonical(type);
        }

        string RequireType(string type)
        {
            string canonical = Canonical(type);
            if (canonical == null) throw new NexusException("Unknown element type '" + type + "'", 400);
            return canonical;
        }

        Element Find(string reference)
        {
            if (string.IsNullOrEmpty(reference)) return null;
            Element element;
            if (reference.StartsWith("=")) return _byRef.TryGetValue(reference, out element) ? element : null;
            return _byRef.Values.FirstOrDefault(e => e.Name != null && string.Equals(e.Name, reference, StringComparison.OrdinalIgnoreCase));
        }

        Element RequireElement(string dbref)
        {
            Element element;
            if (dbref == null || !_byRef.TryGetValue(dbref, out element))
            {
                throw new NexusException("Element " + dbref + " not found", 404);
            }
            return element;
        }

        static object ValueOf(Element element, string attribute)
        {
            switch (attribute.ToUpperInvariant())
            {
                case "NAME": return element.Name;
                case "TYPE": return element.Type;
                case "REF": return element.Dbref;
                case "OWNER": return element.Owner == null ? null : element.Owner.Dbref;
            }
            object value;
            return element.Attributes.TryGetValue(attribute, out value) ? value : null;
        }

        bool IsKnownAttribute(Element element, string attribute)
        {
            string[] udas;
            return SystemAttributes.Contains(attribute, StringComparer.OrdinalIgnoreCase)
                || (_udas.TryGetValue(element.Type, out udas) && udas.Contains(attribute, StringComparer.OrdinalIgnoreCase))
                || element.Attributes.ContainsKey(attribute);
        }

        static IEnumerable<Element> SelfAndBelow(Element root)
        {
            yield return root;
            foreach (Element member in root.Members)
                foreach (Element e in SelfAndBelow(member)) yield return e;
        }

        // Fake "where": only  ATTR eq 'value'  (anything else is what E3D would reject: the collect fails).
        static readonly Regex WhereRx = new Regex(@"^\s*([A-Za-z0-9_:\-]+)\s+eq\s+'([^']*)'\s*$", RegexOptions.IgnoreCase);

        // ---- IAvevaProvider -----------------------------------------------------------------------------------

        public bool ExecuteCommand(string command)
        {
            Executed.Add(command);
            if (FailingLine(command)) throw new NexusException("PML command failed: " + command, 400);
            return true;
        }

        object RequireVariable(string name)
        {
            object value;
            if (!Globals.TryGetValue(name, out value)) throw new VariableNotFoundException(name);
            return value;
        }

        public string GetStringVariable(string name) { return Convert.ToString(RequireVariable(name)); }
        public double GetRealVariable(string name) { return Convert.ToDouble(RequireVariable(name)); }
        public bool GetBooleanVariable(string name) { return Convert.ToBoolean(RequireVariable(name)); }
        public object[] GetArrayVariable(string name) { return ((IEnumerable<object>)RequireVariable(name)).ToArray(); }

        public object GetAttribute(string dbref, string attribute)
        {
            Element element = Find(dbref);
            if (element == null) throw new NexusException("Element " + dbref + " not found", 404);
            // An attribute E3D knows but cannot read on this element: the real GetAsString throws a plain exception (500).
            if (attribute == "UNREADABLE") throw new InvalidOperationException("E3D: attribute UNREADABLE not readable on " + dbref);
            if (!IsKnownAttribute(element, attribute)) throw new NexusException("Unknown attribute '" + attribute + "'", 400);
            return ValueOf(element, attribute);
        }

        public string[] CollectRefs(string type, string scope, string where)
        {
            type = RequireType(type);
            Element root = World;
            if (scope != null)
            {
                root = Find(scope);
                if (root == null) throw new NexusException("Scope '" + scope + "' not found", 404);
            }
            string command = "var !!nexusQ collect all " + type
                + (where != null ? " with (" + where + ")" : "")
                + (scope != null ? " for " + scope : "");

            Func<Element, bool> match = e => true;
            if (where != null)
            {
                Match m = WhereRx.Match(where);
                if (!m.Success) throw new NexusException("PML collect failed (check the where condition and scope): " + command, 400);
                string attribute = m.Groups[1].Value, wanted = m.Groups[2].Value;
                match = e => string.Equals(Convert.ToString(ValueOf(e, attribute)), wanted, StringComparison.OrdinalIgnoreCase);
            }
            return SelfAndBelow(root)
                .Where(e => string.Equals(e.Type, type, StringComparison.OrdinalIgnoreCase) && match(e))
                .Select(e => e.Dbref)
                .ToArray();
        }

        public bool ElementExists(string dbref) { return dbref != null && _byRef.ContainsKey(dbref); }

        public string[] GetChildRefs(string dbref, string type)
        {
            Element element = RequireElement(dbref);
            if (type != null) type = RequireType(type);
            return element.Members
                .Where(m => type == null || string.Equals(m.Type, type, StringComparison.OrdinalIgnoreCase))
                .Select(m => m.Dbref)
                .ToArray();
        }

        public string GetOwnerRef(string dbref)
        {
            Element element = RequireElement(dbref);
            return element.Owner == null ? null : element.Owner.Dbref;
        }

        public string ResolveRef(string reference)
        {
            Element element = Find(reference);
            return element == null ? null : element.Dbref;
        }

        public ResolvedElement[] Resolve(IList<string> references)
        {
            return references.Select(reference =>
            {
                Element element = Find(reference);
                return new ResolvedElement
                {
                    Name = reference,
                    Dbref = element == null ? null : element.Dbref,
                    Type = element == null ? null : element.Type
                };
            }).ToArray();
        }

        public TreeResult GetTree(string dbref, int depth, string type, int limit)
        {
            Element root = RequireElement(dbref);
            string wanted = type == null ? null : RequireType(type);
            var result = new TreeResult { Root = new TreeNode { Dbref = root.Dbref, Type = root.Type, Name = root.Name } };
            result.Truncated = TreeWalk.Walk(
                root,
                e => e.Members,
                e => wanted == null || string.Equals(e.Type, wanted, StringComparison.OrdinalIgnoreCase),
                depth,
                limit,
                (e, owner, level) => result.Items.Add(new TreeItem
                {
                    Dbref = e.Dbref, Type = e.Type, Name = e.Name, Depth = level, Owner = owner.Dbref
                }));
            return result;
        }

        public string[] GetAttributeNames(string type)
        {
            string canonical = RequireType(type);
            return SystemAttributes.Concat(_udas[canonical]).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        }

        public RunResult RunScript(IList<ScriptUnit> units, IList<string> returnVariables)
        {
            var result = new RunResult();
            foreach (ScriptUnit unit in units)
            {
                RunUnits.Add(unit);
                if (unit.Lines.Any(FailingLine))
                {
                    result.Errors.Add(unit.IsBlock
                        ? "PML block failed (lines " + unit.FirstLine + "-" + unit.LastLine + "): " + unit.Lines[0]
                        : "PML command failed: " + unit.Lines[0]);
                }
            }
            foreach (string name in returnVariables)
            {
                object value;
                result.Values[name] = Globals.TryGetValue(name, out value) ? value : null;
            }
            result.Ok = result.Errors.Count == 0;
            return result;
        }

        public SessionInfo GetSessionInfo()
        {
            // a fresh copy, like the real provider (the service sets ReadOnly on it)
            return new SessionInfo
            {
                Project = Session.Project, Mdb = Session.Mdb, User = Session.User,
                Module = Session.Module, E3dVersion = Session.E3dVersion
            };
        }

        public Dictionary<string, object> ReadAttributes(string dbref, IList<string> attributes)
        {
            ReadAttributesCalls++;
            var values = new Dictionary<string, object>();
            Element element = Find(dbref);
            foreach (string name in attributes)
            {
                values[name] = element != null && IsKnownAttribute(element, name) ? ValueOf(element, name) : null;
            }
            return values;
        }
    }
}
