#include "Logging.h"

// The fixture needs only the declaration shape; a real implementation formats and writes the line.
extern "C" void LogMessage(int level, const char* format, ...)
{
    (void)level;
    (void)format;
}
