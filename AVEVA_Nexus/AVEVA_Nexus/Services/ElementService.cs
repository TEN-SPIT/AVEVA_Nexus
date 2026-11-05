using System.Collections.Generic;
using System.Linq;

using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Query;
using AVEVA_Nexus.Validation;

namespace AVEVA_Nexus.Services
{
    public class ElementService
    {
        private const int MaxRefsPerGet = 1000;
        private const int MaxNamesPerResolve = 1000;

        private readonly IAvevaProvider _provider;

        public ElementService() : this(Providers.Create()) { }

        public ElementService(IAvevaProvider provider)
        {
            _provider = provider;
        }

        // dbref or "/NAME" -> dbref of an existing element (400 if malformed, 404 if not found).
        private string RequireRef(string reference)
        {
            return ElementRefs.Require(reference, _provider.ResolveRef);
        }

        public object GetAttribute(
            string dbref,
            string attribute)
        {
            return _provider.GetAttribute(
                RequireRef(dbref),
                PmlInputValidator.Attribute(attribute));
        }

        // Batch read: one row per ref (dbref or name), in input order. Any ref that does not exist fails the whole
        // request (404). The "dbref" column is always the resolved dbref.
        public TableResult Get(GetRequest request)
        {
            if (request == null || request.Refs == null || request.Refs.Count == 0)
            {
                throw new NexusException("refs is required", 400);
            }
            if (request.Refs.Count > MaxRefsPerGet)
            {
                throw new NexusException("too many refs (max " + MaxRefsPerGet + ")", 400);
            }

            List<string> attributes = (request.Attributes ?? new List<string>())
                .Select(PmlInputValidator.Attribute)
                .Distinct()
                .ToList();
            List<string> refs = request.Refs.Select(RequireRef).ToList();

            var result = new TableResult { Total = refs.Count, Offset = 0, Limit = refs.Count };
            result.Columns.Add("dbref");
            result.Columns.AddRange(attributes);

            foreach (string dbref in refs)
            {
                var row = new Dictionary<string, object> { { "dbref", dbref } };
                if (attributes.Count > 0)
                {
                    foreach (var pair in _provider.ReadAttributes(dbref, attributes))
                    {
                        row[pair.Key] = pair.Value;
                    }
                }
                result.Rows.Add(row);
            }
            return result;
        }

        // One entry per input, in order; Dbref/Type null when the input is malformed or names nothing.
        public List<ResolvedElement> Resolve(ResolveRequest request)
        {
            if (request == null || request.Names == null || request.Names.Count == 0)
            {
                throw new NexusException("names is required", 400);
            }
            if (request.Names.Count > MaxNamesPerResolve)
            {
                throw new NexusException("too many names (max " + MaxNamesPerResolve + ")", 400);
            }

            var normalised = request.Names.Select(name =>
            {
                try { return PmlInputValidator.ElementRef(name); }
                catch (NexusException) { return null; }
            }).ToList();

            ResolvedElement[] found = _provider.Resolve(normalised);
            var result = new List<ResolvedElement>();
            for (int i = 0; i < request.Names.Count; i++)
            {
                result.Add(new ResolvedElement
                {
                    Name = request.Names[i],
                    Dbref = found[i].Dbref,
                    Type = found[i].Type
                });
            }
            return result;
        }

        public ChildrenResult Children(string dbref, string type, string limit)
        {
            string reference = RequireRef(dbref);
            string childType = string.IsNullOrWhiteSpace(type) ? null : PmlInputValidator.Type(type);
            int max = Paging.Bounded(limit, 1000, 1, 5000, "limit");

            var result = new ChildrenResult { Owner = _provider.GetOwnerRef(reference) };
            string[] childRefs = _provider.GetChildRefs(reference, childType);
            result.Truncated = childRefs.Length > max;

            var wanted = new List<string> { "TYPE", "NAME" };
            foreach (string childRef in childRefs.Take(max))
            {
                var values = _provider.ReadAttributes(childRef, wanted);
                result.Children.Add(new Dictionary<string, object>
                {
                    { "dbref", childRef },
                    { "type", values["TYPE"] },
                    { "name", values["NAME"] }
                });
            }
            return result;
        }

        public TreeResult Tree(string dbref, string depth, string type, string limit)
        {
            string reference = RequireRef(dbref);
            int levels = Paging.Bounded(depth, 2, 1, 10, "depth");
            int max = Paging.Bounded(limit, 2000, 1, 5000, "limit");
            string itemType = string.IsNullOrWhiteSpace(type) ? null : PmlInputValidator.Type(type);
            return _provider.GetTree(reference, levels, itemType, max);
        }

        public string[] AttributeList(string type)
        {
            return _provider.GetAttributeNames(PmlInputValidator.Type(type));
        }
    }
}
