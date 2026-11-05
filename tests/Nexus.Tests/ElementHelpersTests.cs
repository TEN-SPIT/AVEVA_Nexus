using System.Collections.Generic;
using System.Threading;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Http;
using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Query;
using AVEVA_Nexus.Validation;

namespace Nexus.Tests
{
    public static class ElementHelpersTests
    {
        sealed class Node
        {
            public string Id; public string Type; public List<Node> Kids = new List<Node>();
            public Node(string id, string type, params Node[] kids) { Id = id; Type = type; Kids.AddRange(kids); }
        }

        sealed class DerivedContext : SynchronizationContext { }

        static int Status(System.Action act, string name)
        {
            var ex = Check.Throws<NexusException>(act, name);
            return ex == null ? 0 : ex.StatusCode;
        }

        public static void RunAll()
        {
            Program.Run("ElementRef_normalises_dbrefs_and_accepts_names_with_quotes", () =>
            {
                Check.Equal("=123/45", PmlInputValidator.ElementRef(" =123/45 "), "plain");
                Check.Equal("=123/45", PmlInputValidator.ElementRef("123/45"), "no =");
                Check.Equal("=123/45", PmlInputValidator.ElementRef("=0123/045"), "leading zeros");
                Check.Equal("=0/7", PmlInputValidator.ElementRef("=0/7"), "zero");
                Check.Equal("/14\"-P-1", PmlInputValidator.ElementRef("/14\"-P-1"), "quote in name");
                Check.Equal("/A B'C|D", PmlInputValidator.ElementRef("/A B'C|D"), "spaces and PML quotes");
            });

            Program.Run("ElementRef_rejects_garbage_with_400", () =>
            {
                foreach (var v in new[] { null, "", "/", "PIPE", "=1/", "=a/b", "/A\nB", "/" + new string('X', 600) })
                    Check.Equal(400, Status(() => PmlInputValidator.ElementRef(v), "value " + v), "status " + v);
            });

            Program.Run("ElementRefs_Require_resolves_or_404", () =>
            {
                var known = new Dictionary<string, string> { { "/A", "=1/2" }, { "=1/3", "=1/3" } };
                System.Func<string, string> resolve = r => known.ContainsKey(r) ? known[r] : null;
                Check.Equal("=1/2", ElementRefs.Require("/A", resolve), "name");
                Check.Equal("=1/3", ElementRefs.Require("1/3", resolve), "normalised dbref");
                Check.Equal(404, Status(() => ElementRefs.Require("/MISSING", resolve), "missing"), "404");
                Check.Equal(400, Status(() => ElementRefs.Require("bad", resolve), "bad"), "400");
            });

            Program.Run("TypeNameMap_maps_short_names_and_prefers_full_names", () =>
            {
                var map = new TypeNameMap(new[]
                {
                    new KeyValuePair<string, string>("EQUIPMENT", "EQUI"),
                    new KeyValuePair<string, string>("PIPE", "PIPE"),
                    new KeyValuePair<string, string>("BRANCH", "BRAN"),
                    new KeyValuePair<string, string>("BRAN", "BX"),   // a full name equal to another's short name
                });
                Check.Equal("EQUIPMENT", map.Canonical("EQUIPMENT"), "full");
                Check.Equal("EQUIPMENT", map.Canonical("equi"), "short, any case");
                Check.Equal("PIPE", map.Canonical("PIPE"), "same");
                Check.Equal("BRAN", map.Canonical("BRAN"), "full name wins over short");
                Check.True(map.Canonical("FOO") == null, "unknown");
                Check.True(map.Canonical(null) == null && map.Canonical(" ") == null, "blank");
            });

            Program.Run("TreeWalk_lists_depth_first_with_depth_and_owner", () =>
            {
                var root = new Node("R", "SITE",
                    new Node("Z1", "ZONE", new Node("E1", "EQUI", new Node("N1", "NOZZ"))),
                    new Node("Z2", "ZONE", new Node("P1", "PIPE")));
                var seen = new List<string>();
                bool truncated = TreeWalk.Walk(root, n => n.Kids, n => true, 2, 100,
                    (n, o, d) => seen.Add(n.Id + ":" + d + ":" + o.Id));
                Check.Equal("Z1:1:R|E1:2:Z1|Z2:1:R|P1:2:Z2", string.Join("|", seen), "order, depth 2 stops before N1");
                Check.True(!truncated, "not truncated");
            });

            Program.Run("TreeWalk_type_filter_still_traverses_other_types", () =>
            {
                var root = new Node("R", "SITE",
                    new Node("Z1", "ZONE", new Node("E1", "EQUI")),
                    new Node("Z2", "ZONE", new Node("E2", "EQUI")));
                var seen = new List<string>();
                TreeWalk.Walk(root, n => n.Kids, n => n.Type == "EQUI", 3, 100, (n, o, d) => seen.Add(n.Id + ":" + o.Id));
                Check.Equal("E1:Z1|E2:Z2", string.Join("|", seen), "only EQUI listed");
            });

            Program.Run("TreeWalk_truncates_at_limit", () =>
            {
                var root = new Node("R", "SITE", new Node("A", "X"), new Node("B", "X"), new Node("C", "X"));
                var seen = new List<string>();
                Check.True(TreeWalk.Walk(root, n => n.Kids, n => true, 1, 2, (n, o, d) => seen.Add(n.Id)), "truncated");
                Check.Equal("A|B", string.Join("|", seen), "first two");
                Check.True(!TreeWalk.Walk(root, n => n.Kids, n => true, 1, 3, (n, o, d) => { }), "exact fit is not truncated");
            });

            Program.Run("Paging_Bounded_defaults_and_rejects_out_of_range", () =>
            {
                Check.Equal(2, Paging.Bounded((string)null, 2, 1, 10, "depth"), "default");
                Check.Equal(10, Paging.Bounded("10", 2, 1, 10, "depth"), "max");
                Check.Equal(400, Status(() => Paging.Bounded("11", 2, 1, 10, "depth"), "11"), "above");
                Check.Equal(400, Status(() => Paging.Bounded("0", 2, 1, 10, "depth"), "0"), "below");
                Check.Equal(400, Status(() => Paging.Bounded("x", 2, 1, 10, "depth"), "x"), "not a number");
                Check.Equal(5000, Paging.Bounded((int?)5000, 2000, 1, 5000, "limit"), "int");
            });

            Program.Run("JsonBody_malformed_json_is_400_and_blank_is_null", () =>
            {
                Check.True(JsonBody.Parse<GetRequest>("  ") == null, "blank");
                Check.Equal(1, JsonBody.Parse<GetRequest>("{\"refs\":[\"/A\"]}").Refs.Count, "ok");
                var ex = Check.Throws<NexusException>(() => JsonBody.Parse<GetRequest>("{\"refs\":[\"/A\""), "truncated json");
                Check.True(ex != null && ex.StatusCode == 400 && ex.Message.StartsWith("Malformed JSON body"), "400 with message");
                Check.Equal(400, Status(() => JsonBody.Parse<GetRequest>("{\"refs\":5}"), "wrong shape"), "wrong shape");
            });

            Program.Run("SelectUiContext_auto_uses_only_a_real_message_loop_context", () =>
            {
                var real = new DerivedContext();
                var plain = new SynchronizationContext();
                foreach (var v in new[] { null, "", "auto", "1" })
                {
                    Check.True(AvevaGate.SelectUiContext(v, real) == real, "real context with '" + v + "'");
                    Check.True(AvevaGate.SelectUiContext(v, plain) == null, "base context with '" + v + "'");
                    Check.True(AvevaGate.SelectUiContext(v, null) == null, "no context with '" + v + "'");
                }
                Check.True(AvevaGate.SelectUiContext("0", real) == null, "0 turns it off");
            });
        }
    }
}
