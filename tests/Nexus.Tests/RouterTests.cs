using System;
using System.Collections.Generic;
using System.Linq;
using AVEVA_Nexus.Config;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Http;
using AVEVA_Nexus.Integration;
using AVEVA_Nexus.Models;
using AVEVA_Nexus.Validation;
using Newtonsoft.Json;

namespace Nexus.Tests
{
    // End-to-end through Router (controllers + services + validation) on the in-memory FakeProvider. Status codes
    // are mapped like SimpleHttpServer does: a returned value is 200, NexusException -> its StatusCode, anything
    // else -> 500.
    public static class RouterTests
    {
        sealed class Reply
        {
            public int Status;
            public ApiResponse Response;
            public string Error;
            public object Data { get { return Response == null ? null : Response.Data; } }
            public T As<T>() where T : class { return Data as T; }
        }

        // Model used by every test:
        //   WORLD =0/1
        //     SITE /S1 =1/1
        //       ZONE /Z1 =1/2
        //         EQUI /P1 =1/3 (DESC pump, :TP-MTO-AREA A1)   EQUI /P2 =1/4 (DESC pump)   EQUI /T1 =1/5 (DESC tank)
        //         PIPE /L1 =1/6
        //           BRAN /L1/B1 =1/7
        //     SITE /S2 =1/8
        //       ZONE /Z2 =1/9
        //         EQUI /P3 =1/10
        static FakeProvider Model()
        {
            var m = new FakeProvider();
            var s1 = m.Add(m.World, "SITE", "/S1");
            var z1 = m.Add(s1, "ZONE", "/Z1");
            m.Add(z1, "EQUI", "/P1", "DESC", "pump", ":TP-MTO-AREA", "A1");
            m.Add(z1, "EQUI", "/P2", "DESC", "pump");
            m.Add(z1, "EQUI", "/T1", "DESC", "tank");
            var l1 = m.Add(z1, "PIPE", "/L1");
            m.Add(l1, "BRAN", "/L1/B1");
            var s2 = m.Add(m.World, "SITE", "/S2");
            var z2 = m.Add(s2, "ZONE", "/Z2");
            m.Add(z2, "EQUI", "/P3");
            m.Globals["TXT"] = "hello";
            m.Globals["NUM"] = 2.5;
            m.Globals["FLAG"] = true;
            m.Globals["LIST"] = new object[] { "a", "b" };
            return m;
        }

        static Reply Call(Router router, string method, string path, object body = null, string query = null)
        {
            var context = new RouteContext
            {
                Method = method,
                Path = path,
                Query = RequestGuards.ParseQuery(path + (query == null ? "" : "?" + query)),
                Body = body == null ? "" : body as string ?? JsonConvert.SerializeObject(body)
            };
            try
            {
                object result = router.Route(context);
                return new Reply { Status = 200, Response = result as ApiResponse };
            }
            catch (NexusException ex)
            {
                return new Reply { Status = ex.StatusCode, Error = ex.Message };
            }
            catch (Exception ex)
            {
                return new Reply { Status = 500, Error = ex.GetType().Name + ": " + ex.Message };
            }
        }

        static Reply Call(FakeProvider m, string method, string path, object body = null, string query = null)
        {
            return Call(new Router(m), method, path, body, query);
        }

        static void Status(int expected, Reply reply, string name)
        {
            Check.Equal(expected, reply.Status, name + (reply.Error == null ? "" : " (" + reply.Error + ")"));
        }

        static void ErrorContains(string text, Reply reply, string name)
        {
            Check.True(reply.Error != null && reply.Error.Contains(text), name + ": error was <" + reply.Error + ">");
        }

        static void WithReadOnly(string value, Action body)
        {
            string old = Environment.GetEnvironmentVariable(ReadOnlyMode.VariableName);
            try
            {
                Environment.SetEnvironmentVariable(ReadOnlyMode.VariableName, value);
                body();
            }
            finally
            {
                Environment.SetEnvironmentVariable(ReadOnlyMode.VariableName, old);
            }
        }

        static void Writable(Action body) { WithReadOnly("0", body); }
        static void ReadOnly(Action body) { WithReadOnly(null, body); }

        static List<string> Refs(TableResult table)
        {
            return table.Rows.Select(r => (string)r["dbref"]).ToList();
        }

        public static void RunAll()
        {
            Routing();
            Session();
            Query();
            Get();
            Children();
            Tree();
            Resolve();
            Attributes();
            Pml();
            Batch();
            CompositionRoot();
        }

        static void Routing()
        {
            Program.Run("Router_Health_answers_ok_without_touching_the_provider", () =>
            {
                AvevaGate.Reset();
                var m = Model();
                Reply r = Call(m, "GET", "/api/Health");
                Status(200, r, "status");
                var health = r.As<HealthInfo>();
                Check.True(health != null && health.Status == "ok" && health.Gate != null, "health data");
                Check.Equal(0, m.ReadAttributesCalls, "no provider reads");
            });

            Program.Run("Router_unknown_routes_are_404_and_a_bare_root_is_400", () =>
            {
                var m = Model();
                Status(400, Call(m, "GET", "/api"), "/api");
                Status(404, Call(m, "GET", "/api/Nope"), "unknown module");
                Status(404, Call(m, "GET", "/api/Element/Nope"), "unknown element route");
                Status(404, Call(m, "POST", "/api/PML/Nope"), "unknown pml route");
                Status(404, Call(m, "GET", "/api/PML/Variable/matrix/TXT"), "unknown variable kind");
                Status(404, Call(m, "GET", "/api/Session/Nope"), "unknown session route");
            });

            Program.Run("Router_matches_modules_and_actions_case_insensitively", () =>
            {
                var m = Model();
                Status(200, Call(m, "get", "/API/element/children", query: "dbref=/Z1"), "lower-case method and path");
                Status(200, Call(m, "GET", "/api/SESSION/INFO"), "upper-case");
            });

            Program.Run("Router_wrong_method_is_405_on_every_route", () =>
            {
                var m = Model();
                var cases = new[]
                {
                    new[] { "POST", "/api/Session/Info" }, new[] { "GET", "/api/Element/Query" },
                    new[] { "GET", "/api/Element/Get" }, new[] { "POST", "/api/Element/Children" },
                    new[] { "GET", "/api/Element/Resolve" }, new[] { "POST", "/api/Element/Tree" },
                    new[] { "POST", "/api/Element/AttributeList" }, new[] { "POST", "/api/Element/Attribute" },
                    new[] { "GET", "/api/PML/Run" }, new[] { "GET", "/api/PML/Execute" },
                    new[] { "POST", "/api/PML/Variable/string/TXT" }, new[] { "GET", "/api/Batch" },
                };
                foreach (var c in cases)
                {
                    Reply r = Call(m, c[0], c[1]);
                    Status(405, r, c[0] + " " + c[1]);
                }
            });

            Program.Run("Router_malformed_json_body_is_400_not_500", () =>
            {
                var m = Model();
                Reply r = Call(m, "POST", "/api/Element/Query", "{\"type\": ");
                Status(400, r, "status");
                ErrorContains("Malformed JSON body", r, "message");
            });
        }

        static void Session()
        {
            Program.Run("Session_Info_returns_provider_data_and_the_read_only_flag", () =>
            {
                var m = Model();
                ReadOnly(() =>
                {
                    Reply r = Call(m, "GET", "/api/Session/Info");
                    Status(200, r, "status");
                    var info = r.As<SessionInfo>();
                    Check.True(info != null, "data is SessionInfo");
                    Check.Equal("TST", info.Project, "project");
                    Check.Equal("Design", info.Module, "module");
                    Check.True(info.ReadOnly, "read-only by default");
                    Check.True(r.Response.Success, "success");
                });
                Writable(() => Check.True(!Call(m, "GET", "/api/Session/Info").As<SessionInfo>().ReadOnly, "writable"));
            });
        }

        static void Query()
        {
            Program.Run("Query_requires_a_body_and_a_valid_known_type", () =>
            {
                var m = Model();
                Reply r = Call(m, "POST", "/api/Element/Query");
                Status(400, r, "no body");
                ErrorContains("request body is required", r, "no body message");

                r = Call(m, "POST", "/api/Element/Query", new { type = "EQ UI" });
                Status(400, r, "bad characters");
                ErrorContains("Invalid element type", r, "bad characters message");

                r = Call(m, "POST", "/api/Element/Query", new { type = "FOO" });
                Status(400, r, "unknown type");
                Check.Equal("Unknown element type 'FOO'", r.Error, "unknown type message");

                Status(400, Call(m, "POST", "/api/Element/Query", new { }), "type missing");
            });

            Program.Run("Query_accepts_short_and_full_type_names_and_lists_all_matches", () =>
            {
                var m = Model();
                var shortName = Call(m, "POST", "/api/Element/Query", new { type = "equi" }).As<TableResult>();
                var fullName = Call(m, "POST", "/api/Element/Query", new { type = "EQUIPMENT" }).As<TableResult>();
                Check.Equal(4, shortName.Total, "total by short name");
                Check.Equal("=1/3,=1/4,=1/5,=1/10", string.Join(",", Refs(shortName)), "rows in model order");
                Check.Equal(4, fullName.Total, "total by full name");
                Check.Equal("dbref", string.Join(",", shortName.Columns), "only dbref without attributes");
            });

            Program.Run("Query_where_is_validated_400_before_reaching_E3D", () =>
            {
                var m = Model();
                foreach (string w in new[] { "NAME eq 'A'; DELETE", "!!x eq 1", "DESC.delete()", "SAVEWORK", "(DESC eq 'a'" })
                {
                    Reply r = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", where = w });
                    Status(400, r, "where <" + w + ">");
                    ErrorContains("Invalid where condition", r, "where message <" + w + ">");
                }
            });

            Program.Run("Query_where_filters_and_an_E3D_rejected_where_is_400", () =>
            {
                var m = Model();
                var t = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", where = "DESC eq 'pump'" }).As<TableResult>();
                Check.Equal(2, t.Total, "two pumps");
                Reply r = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", where = "DESC gt 3" });
                Status(400, r, "collect failed");
                ErrorContains("PML collect failed", r, "collect message");
            });

            Program.Run("Query_scope_limits_results_and_an_unknown_scope_is_404", () =>
            {
                var m = Model();
                var t = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", scope = "/S2" }).As<TableResult>();
                Check.Equal(1, t.Total, "one in S2");
                Check.Equal("=1/10", Refs(t)[0], "P3");
                Check.Equal(3, Call(m, "POST", "/api/Element/Query", new { type = "EQUI", scope = "=1/2" }).As<TableResult>().Total, "dbref scope");

                Reply r = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", scope = "/NOPE" });
                Status(404, r, "unknown scope");
                Check.Equal("Element /NOPE not found", r.Error, "unknown scope message");
                Status(400, Call(m, "POST", "/api/Element/Query", new { type = "EQUI", scope = "garbage" }), "malformed scope");
            });

            Program.Run("Query_pages_with_limit_and_offset_and_reports_the_full_total", () =>
            {
                var m = Model();
                var t = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", limit = 2, offset = 1 }).As<TableResult>();
                Check.Equal(4, t.Total, "total");
                Check.Equal(1, t.Offset, "offset");
                Check.Equal(2, t.Limit, "limit");
                Check.Equal("=1/4,=1/5", string.Join(",", Refs(t)), "page");

                var past = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", offset = 10 }).As<TableResult>();
                Check.Equal(4, past.Total, "total past the end");
                Check.Equal(0, past.Rows.Count, "no rows past the end");

                var clamped = Call(m, "POST", "/api/Element/Query", new { type = "EQUI", limit = 0, offset = -5 }).As<TableResult>();
                Check.Equal(1, clamped.Limit, "limit clamped up to 1");
                Check.Equal(0, clamped.Offset, "negative offset -> 0");
                Check.Equal(1, clamped.Rows.Count, "one row");

                Check.Equal(1000, Call(m, "POST", "/api/Element/Query", new { type = "EQUI", limit = 5000 }).As<TableResult>().Limit, "limit clamped to 1000");
                Check.Equal(100, Call(m, "POST", "/api/Element/Query", new { type = "EQUI" }).As<TableResult>().Limit, "default limit 100");
            });

            Program.Run("Query_attributes_become_columns_with_null_for_unknown_and_400_for_invalid_names", () =>
            {
                var m = Model();
                var t = Call(m, "POST", "/api/Element/Query",
                    new { type = "EQUI", attributes = new[] { "name", "DESC", "NAME", "NOSUCH" }, limit = 1 }).As<TableResult>();
                Check.Equal("dbref,NAME,DESC,NOSUCH", string.Join(",", t.Columns), "columns upper-cased and distinct");
                Check.Equal("/P1", (string)t.Rows[0]["NAME"], "name value");
                Check.Equal("pump", (string)t.Rows[0]["DESC"], "desc value");
                Check.True(t.Rows[0].ContainsKey("NOSUCH") && t.Rows[0]["NOSUCH"] == null, "unknown attribute is null");
                Check.Equal(1, m.ReadAttributesCalls, "attributes read for the page only");

                Status(400, Call(m, "POST", "/api/Element/Query", new { type = "EQUI", attributes = new[] { "BAD NAME" } }), "invalid attribute");
            });
        }

        static void Get()
        {
            Program.Run("Get_returns_one_row_per_ref_in_order_with_names_resolved_to_dbrefs", () =>
            {
                var m = Model();
                Reply r = Call(m, "POST", "/api/Element/Get", new { refs = new[] { "/T1", "=1/3", "1/4" }, attributes = new[] { "DESC", ":TP-MTO-AREA" } });
                Status(200, r, "status");
                var t = r.As<TableResult>();
                Check.Equal("=1/5,=1/3,=1/4", string.Join(",", Refs(t)), "order and resolution");
                Check.Equal("dbref,DESC,:TP-MTO-AREA", string.Join(",", t.Columns), "columns");
                Check.Equal("A1", (string)t.Rows[1][":TP-MTO-AREA"], "uda");
                Check.True(t.Rows[0][":TP-MTO-AREA"] == null, "uda unset -> null");
                Check.Equal(3, t.Total, "total");
            });

            Program.Run("Get_unknown_ref_fails_the_whole_request_with_404_and_bad_input_with_400", () =>
            {
                var m = Model();
                Reply r = Call(m, "POST", "/api/Element/Get", new { refs = new[] { "=1/3", "=9/9" } });
                Status(404, r, "unknown ref");
                Check.Equal("Element =9/9 not found", r.Error, "message");
                Status(404, Call(m, "POST", "/api/Element/Get", new { refs = new[] { "/NOPE" } }), "unknown name");
                Status(400, Call(m, "POST", "/api/Element/Get", new { refs = new[] { "xyz" } }), "malformed ref");
                Status(400, Call(m, "POST", "/api/Element/Get", new { refs = new string[0] }), "empty refs");
                Status(400, Call(m, "POST", "/api/Element/Get"), "no body");
            });

            Program.Run("Get_accepts_1000_refs_and_rejects_1001", () =>
            {
                var m = Model();
                Reply ok = Call(m, "POST", "/api/Element/Get", new { refs = Enumerable.Repeat("=1/3", 1000).ToArray() });
                Status(200, ok, "1000");
                Check.Equal(1000, ok.As<TableResult>().Rows.Count, "1000 rows");
                Reply tooMany = Call(m, "POST", "/api/Element/Get", new { refs = Enumerable.Repeat("=1/3", 1001).ToArray() });
                Status(400, tooMany, "1001");
                Check.Equal("too many refs (max 1000)", tooMany.Error, "message");
            });
        }

        static void Children()
        {
            Program.Run("Children_lists_members_with_type_and_name_and_the_owner", () =>
            {
                var m = Model();
                Reply r = Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1");
                Status(200, r, "status");
                var c = r.As<ChildrenResult>();
                Check.Equal("=1/1", c.Owner, "owner");
                Check.Equal(4, c.Children.Count, "count");
                Check.True(!c.Truncated, "not truncated");
                Check.Equal("=1/3", (string)c.Children[0]["dbref"], "first dbref");
                Check.Equal("EQUIPMENT", (string)c.Children[0]["type"], "first type");
                Check.Equal("/P1", (string)c.Children[0]["name"], "first name");
                Check.Equal(3, Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&type=EQUI").As<ChildrenResult>().Children.Count, "type filter");
            });

            Program.Run("Children_limit_truncates_and_is_bounded", () =>
            {
                var m = Model();
                var c = Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&limit=2").As<ChildrenResult>();
                Check.Equal(2, c.Children.Count, "two");
                Check.True(c.Truncated, "truncated");
                Check.True(!Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&limit=4").As<ChildrenResult>().Truncated, "exact limit not truncated");
                Status(400, Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&limit=0"), "limit 0");
                Status(400, Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&limit=5001"), "limit 5001");
                Status(400, Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&limit=abc"), "limit abc");
            });

            Program.Run("Children_of_WORLD_has_no_owner_and_errors_map_to_400_404", () =>
            {
                var m = Model();
                var w = Call(m, "GET", "/api/Element/Children", query: "dbref=%3D0/1").As<ChildrenResult>();
                Check.True(w != null && w.Owner == null, "WORLD owner is null");
                Check.Equal(2, w.Children.Count, "two sites");
                Status(404, Call(m, "GET", "/api/Element/Children", query: "dbref=%3D7/7"), "unknown dbref");
                Status(400, Call(m, "GET", "/api/Element/Children"), "dbref missing");
                Reply r = Call(m, "GET", "/api/Element/Children", query: "dbref=/Z1&type=FOO");
                Status(400, r, "unknown type");
                Check.Equal("Unknown element type 'FOO'", r.Error, "unknown type message");
            });
        }

        static void Tree()
        {
            Program.Run("Tree_walks_pre_order_to_the_default_depth_2", () =>
            {
                var m = Model();
                Reply r = Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1");
                Status(200, r, "status");
                var t = r.As<TreeResult>();
                Check.Equal("=1/1", t.Root.Dbref, "root");
                Check.Equal("SITE", t.Root.Type, "root type");
                Check.Equal("=1/2,=1/3,=1/4,=1/5,=1/6", string.Join(",", t.Items.Select(i => i.Dbref)), "items");
                Check.Equal(1, t.Items[0].Depth, "zone depth");
                Check.Equal(2, t.Items[1].Depth, "equi depth");
                Check.Equal("=1/2", t.Items[1].Owner, "equi owner");
                Check.True(!t.Truncated, "not truncated");
            });

            Program.Run("Tree_depth_type_filter_and_limit", () =>
            {
                var m = Model();
                Check.Equal(1, Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&depth=1").As<TreeResult>().Items.Count, "depth 1");
                var bran = Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&depth=3&type=BRAN").As<TreeResult>();
                Check.Equal(1, bran.Items.Count, "type filters listing, not traversal");
                Check.Equal("=1/6", bran.Items[0].Owner, "branch owner");
                Check.Equal(3, bran.Items[0].Depth, "branch depth");

                var limited = Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&limit=2").As<TreeResult>();
                Check.Equal(2, limited.Items.Count, "limit 2");
                Check.True(limited.Truncated, "truncated");

                Status(400, Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&depth=11"), "depth 11");
                Status(400, Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&depth=0"), "depth 0");
                Status(400, Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&limit=5001"), "limit 5001");
                Status(400, Call(m, "GET", "/api/Element/Tree", query: "dbref=/S1&type=FOO"), "unknown type");
                Status(404, Call(m, "GET", "/api/Element/Tree", query: "dbref=/NOPE"), "unknown root");
            });
        }

        static void Resolve()
        {
            Program.Run("Resolve_gives_one_entry_per_input_with_null_for_unknown_or_malformed", () =>
            {
                var m = Model();
                Reply r = Call(m, "POST", "/api/Element/Resolve", new { names = new[] { "/P1", "/NOPE", "not a ref", "1/10" } });
                Status(200, r, "status");
                var list = r.Data as List<ResolvedElement>;
                Check.True(list != null && list.Count == 4, "four entries");
                Check.Equal("/P1", list[0].Name, "input echoed");
                Check.Equal("=1/3", list[0].Dbref, "found");
                Check.Equal("EQUIPMENT", list[0].Type, "type");
                Check.True(list[1].Dbref == null && list[1].Type == null, "unknown -> null");
                Check.True(list[2].Dbref == null && list[2].Name == "not a ref", "malformed -> null, name kept");
                Check.Equal("=1/10", list[3].Dbref, "loose dbref normalised");
            });

            Program.Run("Resolve_requires_names_and_caps_them_at_1000", () =>
            {
                var m = Model();
                Status(400, Call(m, "POST", "/api/Element/Resolve", new { names = new string[0] }), "empty");
                Status(400, Call(m, "POST", "/api/Element/Resolve"), "no body");
                Status(200, Call(m, "POST", "/api/Element/Resolve", new { names = Enumerable.Repeat("/P1", 1000).ToArray() }), "1000");
                Reply r = Call(m, "POST", "/api/Element/Resolve", new { names = Enumerable.Repeat("/P1", 1001).ToArray() });
                Status(400, r, "1001");
                Check.Equal("too many names (max 1000)", r.Error, "message");
            });
        }

        static void Attributes()
        {
            Program.Run("AttributeList_lists_system_attributes_and_udas_of_a_type", () =>
            {
                var m = Model();
                Reply r = Call(m, "GET", "/api/Element/AttributeList", query: "type=EQUI");
                Status(200, r, "status");
                var names = r.Data as string[];
                Check.True(names != null && names.Contains("NAME") && names.Contains(":TP-MTO-AREA"), "names");
                Reply unknown = Call(m, "GET", "/api/Element/AttributeList", query: "type=FOO");
                Status(400, unknown, "unknown type");
                Check.Equal("Unknown element type 'FOO'", unknown.Error, "message");
                Status(400, Call(m, "GET", "/api/Element/AttributeList"), "type missing");
            });

            Program.Run("Attribute_reads_one_value_and_validates_its_parameters", () =>
            {
                var m = Model();
                Reply r = Call(m, "GET", "/api/Element/Attribute", query: "dbref=/P1&attribute=desc");
                Status(200, r, "status");
                Check.Equal("pump", r.Data as string, "value");
                Status(400, Call(m, "GET", "/api/Element/Attribute", query: "dbref=/P1"), "attribute missing");
                Status(400, Call(m, "GET", "/api/Element/Attribute", query: "attribute=NAME"), "dbref missing");
                Status(400, Call(m, "GET", "/api/Element/Attribute", query: "dbref=/P1&attribute=BAD%20NAME"), "invalid attribute");
                Status(404, Call(m, "GET", "/api/Element/Attribute", query: "dbref=/NOPE&attribute=NAME"), "unknown element");
                // Unknown-but-valid attribute name: 400 (it used to be a NullReferenceException, a 500).
                Status(400, Call(m, "GET", "/api/Element/Attribute", query: "dbref=/P1&attribute=NOSUCH"), "unknown attribute");
            });
        }

        static void Pml()
        {
            Program.Run("PML_Run_and_Execute_are_403_in_read_only_mode_and_run_nothing", () => ReadOnly(() =>
            {
                var m = Model();
                Reply run = Call(m, "POST", "/api/PML/Run", new { script = "!!a = 1" });
                Status(403, run, "run");
                Check.Equal(ReadOnlyMode.BlockedMessage, run.Error, "run message");
                Status(403, Call(m, "POST", "/api/PML/Execute", new { command = "!!a = 1" }), "execute");
                Check.Equal(0, m.RunUnits.Count + m.Executed.Count, "nothing ran");
            }));

            Program.Run("PML_Run_records_failed_lines_and_reads_back_variables", () => Writable(() =>
            {
                var m = Model();
                Reply r = Call(m, "POST", "/api/PML/Run",
                    new { script = "!!a = 1\n-- comment\nFAIL here\nif (true) then\n  !!b = 2\nendif", @return = new[] { "TXT", "MISSING", "TXT" } });
                Status(200, r, "status");
                Check.True(r.Response.Success, "success stays true");
                var result = r.As<RunResult>();
                Check.True(!result.Ok, "not ok");
                Check.Equal(1, result.Errors.Count, "one error");
                Check.Equal("PML command failed: FAIL here", result.Errors[0], "error text");
                Check.Equal(3, m.RunUnits.Count, "two lines and one block");
                Check.True(m.RunUnits[2].IsBlock, "if-block as one unit");
                Check.Equal("hello", result.Values["TXT"] as string, "value read back");
                Check.True(result.Values.ContainsKey("MISSING") && result.Values["MISSING"] == null, "undefined -> null");
                Check.Equal(2, result.Values.Count, "names distinct");
            }));

            Program.Run("PML_Run_validates_script_and_return_names", () => Writable(() =>
            {
                var m = Model();
                Status(400, Call(m, "POST", "/api/PML/Run", new { script = "  " }), "blank script");
                Status(400, Call(m, "POST", "/api/PML/Run"), "no body");
                Status(400, Call(m, "POST", "/api/PML/Run", new { script = "!!a = 1", @return = new[] { "!!a" } }), "bad return name");
                string longScript = string.Join("\n", Enumerable.Repeat("!!a = 1", 501));
                Reply r = Call(m, "POST", "/api/PML/Run", new { script = longScript });
                Status(400, r, "501 lines");
                Check.Equal("script too long (max 500 lines)", r.Error, "message");
                Check.Equal(0, m.RunUnits.Count, "nothing ran");
            }));

            Program.Run("PML_Execute_runs_one_command_and_maps_failures_to_400", () => Writable(() =>
            {
                var m = Model();
                Reply ok = Call(m, "POST", "/api/PML/Execute", new { command = "!!a = 1" });
                Status(200, ok, "ok");
                Check.True(ok.Response.Success, "success");
                Check.Equal("!!a = 1", m.Executed.Single(), "executed");
                Reply bad = Call(m, "POST", "/api/PML/Execute", new { command = "FAIL" });
                Status(400, bad, "failing command");
                Check.Equal("PML command failed: FAIL", bad.Error, "message");
                Status(400, Call(m, "POST", "/api/PML/Execute", new { command = "" }), "command required");
            }));

            Program.Run("PML_Variable_reads_typed_globals_and_404s_undefined_ones", () => ReadOnly(() =>
            {
                var m = Model();
                Check.Equal("hello", Call(m, "GET", "/api/PML/Variable/string/TXT").Data as string, "string");
                Check.Equal(2.5, (double)Call(m, "GET", "/api/PML/Variable/real/NUM").Data, "real");
                Check.Equal(true, (bool)Call(m, "GET", "/api/PML/Variable/boolean/FLAG").Data, "boolean");
                Check.Equal(2, ((object[])Call(m, "GET", "/api/PML/Variable/Array/LIST").Data).Length, "array");
                Reply r = Call(m, "GET", "/api/PML/Variable/string/NOPE");
                Status(404, r, "undefined");
                Check.Equal("Variable 'NOPE' was not found.", r.Error, "message");
                Status(400, Call(m, "GET", "/api/PML/Variable/string/a$b"), "invalid name");
            }));
        }

        static void Batch()
        {
            Program.Run("Batch_through_Router_returns_one_status_per_item_in_read_only_mode", () => ReadOnly(() =>
            {
                var m = Model();
                var body = new
                {
                    requests = new object[]
                    {
                        new { method = "GET", path = "/api/Session/Info" },
                        new { method = "GET", path = "/api/Element/Children?dbref=/Z1&limit=2" },
                        new { method = "POST", path = "/api/Element/Get", body = new { refs = new[] { "=9/9" } } },
                        new { method = "POST", path = "/api/PML/Run", body = new { script = "!!a = 1" } },
                        new { method = "GET", path = "/api/Element/Query" },
                        new { method = "GET", path = "/api/Nope" },
                        new { method = "PUT", path = "/api/Session/Info" },
                        new { method = "GET", path = "/api/Element/Tree", query = new { dbref = "/S1", depth = 1 } },
                        new { method = "GET", path = "/api/Element/Attribute?dbref=/P1&attribute=UNREADABLE" },
                    }
                };
                Reply r = Call(m, "POST", "/api/Batch", body);
                Status(200, r, "batch itself");
                var items = r.Data as List<BatchItemResult>;
                Check.True(items != null && items.Count == 9, "nine items");
                Check.Equal("200,200,404,403,405,404,400,200,500", string.Join(",", items.Select(i => i.Status)), "statuses");
                Check.True(((ChildrenResult)items[1].Data).Truncated, "children limit from the path query");
                Check.Equal(1, ((TreeResult)items[7].Data).Items.Count, "tree depth from the query object");
                Check.Equal(ReadOnlyMode.BlockedMessage, items[3].Error, "read-only applies inside a batch");
                Check.Equal("Internal Server Error", items[8].Error, "500 details are not leaked");
                Check.Equal(0, m.RunUnits.Count, "nothing ran");
            }));

            Program.Run("Batch_runs_PML_when_writable_and_reports_Execute_failures_per_item", () => Writable(() =>
            {
                var m = Model();
                var body = new
                {
                    requests = new object[]
                    {
                        new { method = "POST", path = "/api/PML/Run", body = new { script = "!!a = 1", @return = new[] { "NUM" } } },
                        new { method = "POST", path = "/api/PML/Execute", body = "{\"command\":\"FAIL\"}" },
                        new { method = "POST", path = "/api/PML/Execute", body = new { command = "!!b = 2" } },
                    }
                };
                var items = Call(m, "POST", "/api/Batch", body).Data as List<BatchItemResult>;
                Check.Equal("200,400,200", string.Join(",", items.Select(i => i.Status)), "statuses");
                Check.Equal(2.5, (double)((RunResult)items[0].Data).Values["NUM"], "run value");
                Check.Equal("!!b = 2", m.Executed.Last(), "later items still run");
            }));

            Program.Run("Batch_structural_errors_fail_the_whole_batch_before_anything_runs", () => Writable(() =>
            {
                var m = Model();
                Reply nested = Call(m, "POST", "/api/Batch", new
                {
                    requests = new object[]
                    {
                        new { method = "POST", path = "/api/PML/Execute", body = new { command = "!!a = 1" } },
                        new { method = "POST", path = "/api/Batch", body = new { requests = new object[0] } },
                    }
                });
                Status(400, nested, "nested");
                ErrorContains("nested Batch", nested, "nested message");
                Status(400, Call(m, "POST", "/api/Batch", new { requests = new object[0] }), "empty");
                var tooMany = Enumerable.Repeat((object)new { method = "GET", path = "/api/Session/Info" }, 51).ToArray();
                Status(400, Call(m, "POST", "/api/Batch", new { requests = tooMany }), "51 requests");
                var bigGets = Enumerable.Range(0, 6)
                    .Select(i => (object)new { method = "POST", path = "/api/Element/Get", body = new { refs = Enumerable.Repeat("=1/3", 1000).ToArray() } })
                    .ToArray();
                Reply budget = Call(m, "POST", "/api/Batch", new { requests = bigGets });
                Status(400, budget, "ref budget");
                ErrorContains("batch too large", budget, "budget message");
                Check.Equal(0, m.Executed.Count, "nothing ran");
            }));
        }

        static void CompositionRoot()
        {
            Program.Run("Default_Router_takes_its_provider_from_Providers_Factory", () =>
            {
                var m = Model();
                Func<IAvevaProvider> old = Providers.Factory;
                try
                {
                    int created = 0;
                    Providers.Factory = () => { created++; return m; };
                    Reply r = Call(new Router(), "GET", "/api/Session/Info");
                    Status(200, r, "status");
                    Check.Equal("TST", r.As<SessionInfo>().Project, "fake used");
                    Check.True(created >= 3, "one provider per service, as in production (" + created + ")");
                    Status(200, Call(new Router(), "POST", "/api/Element/Query", new { type = "EQUI" }), "query via default path");
                }
                finally
                {
                    Providers.Factory = old;
                }
            });

            Program.Run("Providers_without_a_factory_fail_clearly_in_the_AVEVA_free_build", () =>
            {
                Func<IAvevaProvider> old = Providers.Factory;
                try
                {
                    Providers.Factory = null;
                    var ex = Check.Throws<InvalidOperationException>(() => Providers.Create(), "no default provider");
                    Check.True(ex == null || ex.Message.Contains("Providers.Factory"), "message names the fix");
                    Check.Throws<ArgumentNullException>(() => new Router(null), "null provider rejected");
                }
                finally
                {
                    Providers.Factory = old;
                }
            });
        }
    }
}
