/* Native macOS entry point; runs the monitor in-process under its own name. */
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], resolved[PATH_MAX], script[PATH_MAX];
    uint32_t size = sizeof(executable);
    if (_NSGetExecutablePath(executable, &size) || !realpath(executable, resolved)) {
        fputs("gold-monitor: cannot resolve executable path\n", stderr);
        return 1;
    }
    char *slash = strrchr(resolved, '/');
    if (!slash) return 1;
    *slash = '\0';
    if (snprintf(script, sizeof(script), "%s/gold_monitor.py", resolved) >= (int)sizeof(script))
        return 1;
    setenv("PYTHONHOME", PYTHON_HOME, 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);
    void *runtime = dlopen(PYTHON_LIBRARY, RTLD_NOW | RTLD_GLOBAL);
    if (!runtime) {
        fprintf(stderr, "gold-monitor: cannot load runtime: %s\n", dlerror());
        return 1;
    }
    int (*python_main)(int, char **) = (int (*)(int, char **))dlsym(runtime, "Py_BytesMain");
    if (!python_main) {
        fputs("gold-monitor: runtime entry point unavailable\n", stderr);
        return 1;
    }
    char **args = calloc((size_t)argc + 2, sizeof(char *));
    if (!args) return 1;
    args[0] = argv[0];
    args[1] = getenv("GOLD_MONITOR_SCRIPT");
    if (!args[1]) args[1] = script;
    for (int i = 1; i < argc; ++i) args[i + 1] = argv[i];
    int result = python_main(argc + 1, args);
    free(args);
    return result;
}
