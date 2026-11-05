using AVEVA_Nexus.Exceptions;
using System.Collections.Generic;

namespace AVEVA_Nexus.Query
{
    public static class Paging
    {
        public const int DefaultLimit = 100;
        public const int MaxLimit = 1000;

        public static (int offset, int limit) Normalize(int? offset, int? limit)
        {
            int o = offset.HasValue && offset.Value > 0 ? offset.Value : 0;
            int l = limit ?? DefaultLimit;
            if (l < 1) l = 1;
            if (l > MaxLimit) l = MaxLimit;
            return (o, l);
        }

        // Optional integer parameter: the default when absent, 400 when outside [min, max].
        public static int Bounded(int? value, int defaultValue, int min, int max, string name)
        {
            int v = value ?? defaultValue;
            if (v < min || v > max)
            {
                throw new NexusException(name + " must be between " + min + " and " + max, 400);
            }
            return v;
        }

        // Same, from a query-string value (null/empty -> default; not a number -> 400).
        public static int Bounded(string value, int defaultValue, int min, int max, string name)
        {
            if (string.IsNullOrWhiteSpace(value)) return Bounded((int?)null, defaultValue, min, max, name);
            int parsed;
            if (!int.TryParse(value.Trim(), out parsed))
            {
                throw new NexusException(name + " must be an integer", 400);
            }
            return Bounded(parsed, defaultValue, min, max, name);
        }

        public static List<T> Slice<T>(IList<T> all, int offset, int limit)
        {
            var page = new List<T>();
            for (int i = offset; i < all.Count && page.Count < limit; i++) page.Add(all[i]);
            return page;
        }
    }
}
