#pragma once

namespace MiniPlant
{
namespace Native
{
enum LogLevel
{
    LOG_DEBUG = 0,
    LOG_INFO = 1,
    LOG_WARN = 2,
    LOG_ERROR = 3
};
}
}

// printf-style logging function; configured in codegraph.yaml under native_log_functions.
extern "C" void LogMessage(int level, const char* format, ...);

// After macro expansion the call targets LogMessage, which is what the extractor sees.
#define MP_LOG_ERROR(...) LogMessage(MiniPlant::Native::LOG_ERROR, __VA_ARGS__)
