using System.Collections.Generic;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Http;
using AVEVA_Nexus.Models;

namespace Nexus.Tests
{
    public static class BatchRunnerTests
    {
        static string Requests(int n, string item)
        {
            var parts = new List<string>();
            for (int i = 0; i < n; i++) parts.Add(item);
            return "{\"requests\":[" + string.Join(",", parts) + "]}";
        }

        static int Status(System.Action act, string name)
        {
            var ex = Check.Throws<NexusException>(act, name);
            return ex == null ? 0 : ex.StatusCode;
        }

        public static void RunAll()
        {
            Program.Run("Batch_runs_sub_requests_in_order_and_keeps_going_after_a_failure", () =>
            {
                var seen = new List<string>();
                var results = BatchRunner.Run(
                    "{\"requests\":[{\"method\":\"GET\",\"path\":\"/api/A\"},{\"method\":\"POST\",\"path\":\"/api/Fail\"},{\"path\":\"/api/C\"}]}",
                    ctx =>
                    {
                        seen.Add(ctx.Method + " " + ctx.Path);
                        if (ctx.Path == "/api/Fail") throw new NexusException("nope", 404);
                        return new ApiResponse { Success = true, Data = ctx.Path };
                    });
                Check.Equal(3, results.Count, "count");
                Check.Equal("GET /api/A|POST /api/Fail|GET /api/C", string.Join("|", seen), "order and default GET");
                Check.Equal(200, results[0].Status, "first status");
                Check.Equal("/api/A", (string)results[0].Data, "first data");
                Check.True(results[0].Error == null, "first no error");
                Check.Equal(404, results[1].Status, "second status");
                Check.Equal("nope", results[1].Error, "second error");
                Check.Equal(200, results[2].Status, "third still runs");
            });

            Program.Run("Batch_unexpected_exception_is_500_without_details", () =>
            {
                var results = BatchRunner.Run("{\"requests\":[{\"path\":\"/api/X\"}]}",
                    ctx => { throw new System.InvalidOperationException("secret at line 3"); });
                Check.Equal(500, results[0].Status, "status");
                Check.Equal("Internal Server Error", results[0].Error, "error hidden");
            });

            Program.Run("Batch_passes_query_and_body_to_the_router", () =>
            {
                RouteContext got = null;
                BatchRunner.Run(
                    "{\"requests\":[{\"method\":\"post\",\"path\":\"/api/Element/Children?type=ZONE\",\"query\":{\"dbref\":\"=1/2\",\"limit\":5},\"body\":{\"names\":[\"/A\"]}}]}",
                    ctx => { got = ctx; return new ApiResponse { Success = true }; });
                Check.Equal("POST", got.Method, "method upper-cased");
                Check.Equal("/api/Element/Children", got.Path, "path without query string");
                Check.Equal("ZONE", got.Query["type"], "query string in path");
                Check.Equal("=1/2", got.Query["dbref"], "query object");
                Check.Equal("5", got.Query["limit"], "number as text");
                Check.Equal("{\"names\":[\"/A\"]}", got.Body, "body as JSON text");
            });

            Program.Run("Batch_rejects_more_than_50_requests_with_400", () =>
            {
                Check.Equal(400, Status(() => BatchRunner.Run(Requests(51, "{\"path\":\"/api/A\"}"), ctx => null), "51"), "status");
                Check.Equal(50, BatchRunner.Run(Requests(50, "{\"path\":\"/api/A\"}"), ctx => null).Count, "50 ok");
            });

            Program.Run("Batch_rejects_nested_batch_before_running_anything", () =>
            {
                int calls = 0;
                Check.Equal(400, Status(() => BatchRunner.Run(
                    "{\"requests\":[{\"path\":\"/api/A\"},{\"method\":\"POST\",\"path\":\"/api/batch/\"}]}",
                    ctx => { calls++; return null; }), "nested"), "status");
                Check.Equal(0, calls, "nothing ran");
            });

            Program.Run("Batch_rejects_empty_or_malformed_body_with_400", () =>
            {
                foreach (var body in new[] { "", "{}", "{\"requests\":[]}", "{not json", "{\"requests\":5}" })
                    Check.Equal(400, Status(() => BatchRunner.Run(body, ctx => null), "body " + body), "status for " + body);
            });

            Program.Run("Batch_bad_method_or_path_fails_only_that_item", () =>
            {
                var results = BatchRunner.Run(
                    "{\"requests\":[{\"method\":\"DELETE\",\"path\":\"/api/A\"},{\"path\":\"Element/Get\"},{\"path\":\"/api/B\"}]}",
                    ctx => new ApiResponse { Success = true, Data = 1 });
                Check.Equal(400, results[0].Status, "method");
                Check.Equal(400, results[1].Status, "path");
                Check.Equal(200, results[2].Status, "good one");
            });

            Program.Run("Batch_unsuccessful_ApiResponse_reports_its_error", () =>
            {
                var results = BatchRunner.Run("{\"requests\":[{\"path\":\"/api/A\"}]}",
                    ctx => new ApiResponse { Success = false, Error = "bad" });
                Check.Equal(200, results[0].Status, "status");
                Check.Equal("bad", results[0].Error, "error");
            });
        }
    }
}
