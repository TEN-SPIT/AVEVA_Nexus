using AVEVA_Nexus.Controllers;
using AVEVA_Nexus.Exceptions;
using AVEVA_Nexus.Logging;
using AVEVA_Nexus.Models;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;

namespace AVEVA_Nexus.Http
{
    public class Router
    {
        private readonly PmlController _pml;
        private readonly ElementController _element;
        private readonly SessionController _session;

        public Router()
        {
            _pml = new PmlController();
            _element = new ElementController();
            _session = new SessionController();
        }

        //public object Route(RouteContext context)
        //{
        //    if (path == "/api/pml/variable/string/test")
        //    {
        //        return _pml.GetString("Test");
        //    }
        //
        //    if (path == "/api/pml/variable/real/test")
        //    {
        //        return _pml.GetReal("Test");
        //    }
        //
        //    if (path == "/api/pml/variable/boolean/test")
        //    {
        //        return _pml.GetBoolean("Test");
        //    }
        //
        //    if (path == "/api/pml/variable/array/test")
        //    {
        //        return _pml.GetArray("Test");
        //    }
        //
        //    if (path == "/api/element/attribute/test")
        //    {
        //        return _element.GetAttribute(
        //            "=1234/5678",
        //            "NAME");
        //    }
        //
        //    throw new NexusException(
        //        "Route Not Found",
        //        404);
        //}


        public object Route(RouteContext context)
        {
            Trace.Line("========================================");
            Trace.Line("ROUTER INVOKED");
            Trace.Line("========================================");

            Trace.Line($"Method      : {context.Method}");
            Trace.Line($"Path        : {context.Path}");
            Trace.Line($"Body        : {context.Body}");

            if (context.Query != null)
            {
                Trace.Line("Query Parameters:");

                foreach (string key in context.Query.Keys)
                {
                    Trace.Line($"  {key} = {context.Query[key]}");
                }
            }

            string[] segments =
                context.Path
                    .Trim('/')
                    .Split('/');

            Trace.Line($"Segment Count : {segments.Length}");

            for (int i = 0; i < segments.Length; i++)
            {
                Trace.Line($"Segment[{i}] = '{segments[i]}'");
            }

            if (segments.Length < 2)
            {
                throw new NexusException(
                    $"Invalid route. Segment count = {segments.Length}",
                    400);
            }

            if (segments[1].Equals("Health", StringComparison.OrdinalIgnoreCase))
            {
                return new ApiResponse { Success = true, Data = "ok" };
            }

            Trace.Line($"Root Segment = {segments[0]}");
            Trace.Line($"Module       = {segments[1]}");

            if (segments[1].Equals("Batch", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "POST");
                // Sub-requests go through Route again (same read-only rules); BatchRunner refuses nested batches.
                return new ApiResponse { Success = true, Data = BatchRunner.Run(context.Body, Route) };
            }

            if (segments[1].Equals(
                "PML",
                StringComparison.OrdinalIgnoreCase))
            {
                Trace.Line("Routing to PML");
                return RoutePml(context, segments);
            }

            if (segments[1].Equals(
                "ELEMENT",
                StringComparison.OrdinalIgnoreCase))
            {
                Trace.Line("Routing to Element");
                return RouteElement(context, segments);
            }

            if (segments[1].Equals("Session", StringComparison.OrdinalIgnoreCase) &&
                segments.Length >= 3 &&
                segments[2].Equals("Info", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "GET");
                return _session.Info();
            }

            throw new NexusException(
                $"Route Not Found. Module '{segments[1]}' not supported.",
                404);
        }


        private object RoutePml(
            RouteContext context,
            string[] segments)
        {
            if (segments.Length >= 3 &&
                segments[2].Equals(
                    "Execute",
                    StringComparison.OrdinalIgnoreCase))
            {
                return _pml.Execute(
                    context.Body);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals("Run", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "POST");
                return _pml.Run(context.Body);
            }

            if (segments.Length >= 5 &&
                segments[2].Equals("Variable",
                StringComparison.OrdinalIgnoreCase))
            {
                string variableType =
                    segments[3];

                string variableName =
                    segments[4];

                switch (variableType.ToLower())
                {
                    case "string":
                        return _pml.GetString(
                            variableName);

                    case "real":
                        return _pml.GetReal(
                            variableName);

                    case "boolean":
                        return _pml.GetBoolean(
                            variableName);

                    case "array":
                        return _pml.GetArray(
                            variableName);
                }
            }

            throw new NexusException(
                "Unsupported PML Route",
                404);
        }


        private static void RequireMethod(RouteContext context, string method)
        {
            if (!string.Equals(context.Method, method, StringComparison.OrdinalIgnoreCase))
            {
                throw new NexusException($"Method {context.Method} not allowed; use {method}", 405);
            }
        }

        private object RouteElement(
            RouteContext context,
            string[] segments)
        {
            Trace.Line("Inside RouteElement");

            if (segments.Length >= 3 &&
                segments[2].Equals("Query", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "POST");
                return _element.Query(context.Body);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals("Get", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "POST");
                return _element.Get(context.Body);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals("Children", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "GET");
                return _element.Children(context.Query["dbref"], context.Query["type"], context.Query["limit"]);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals("Resolve", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "POST");
                return _element.Resolve(context.Body);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals("Tree", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "GET");
                return _element.Tree(context.Query["dbref"], context.Query["depth"], context.Query["type"], context.Query["limit"]);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals("AttributeList", StringComparison.OrdinalIgnoreCase))
            {
                RequireMethod(context, "GET");
                return _element.AttributeList(context.Query["type"]);
            }

            if (segments.Length >= 3 &&
                segments[2].Equals(
                    "Attribute",
                    StringComparison.OrdinalIgnoreCase))
            {
                string dbref =
                    context.Query["dbref"];

                string attribute =
                    context.Query["attribute"];

                Trace.Line($"DBREF     : {dbref}");
                Trace.Line($"ATTRIBUTE : {attribute}");

                if (string.IsNullOrWhiteSpace(dbref))
                {
                    throw new NexusException(
                        "dbref parameter missing",
                        400);
                }

                if (string.IsNullOrWhiteSpace(attribute))
                {
                    throw new NexusException(
                        "attribute parameter missing",
                        400);
                }

                return _element.GetAttribute(
                    dbref,
                    attribute);
            }

            throw new NexusException(
                "Unsupported Element Route",
                404);
        }
    }
}