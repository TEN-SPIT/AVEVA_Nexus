using System;

namespace AVEVA_Nexus.Integration
{
    // Composition root for the E3D provider. Every default constructor (services, controllers, new Router()) gets
    // its provider from Create(), so production keeps building one AvevaProvider per service exactly as before.
    // Factory lets a test (or another host) supply a different IAvevaProvider; when it is null the real
    // AvevaProvider is used. That default comes from a partial method implemented next to AvevaProvider (in
    // AvevaProvider.cs), so this file has no Aveva.* dependency and is compiled by the unit-test runner too.
    public static partial class Providers
    {
        public static Func<IAvevaProvider> Factory { get; set; }

        public static IAvevaProvider Create()
        {
            Func<IAvevaProvider> factory = Factory;
            if (factory != null)
            {
                return factory();
            }

            IAvevaProvider provider = null;
            CreateDefault(ref provider);
            if (provider == null)
            {
                throw new InvalidOperationException(
                    "No IAvevaProvider available: AvevaProvider is not compiled in and Providers.Factory is not set");
            }
            return provider;
        }

        // Implemented in AvevaProvider.cs (new AvevaProvider()); absent in the AVEVA-free test build.
        static partial void CreateDefault(ref IAvevaProvider provider);
    }
}
