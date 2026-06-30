#ifndef KYVP_LOGGING_H_
#define KYVP_LOGGING_H_
#include "experimental/KYVPExperimental.h"
#include "KYVPDefines.h"

#include <string.h>
#include <ctype.h>

typedef enum _KYVPLOG_PRIORITY
{
    KYVPLOG_TRACE_LEVEL = 8,
    KYVPLOG_DEBUG_LEVEL = 7,
    KYVPLOG_INFO_LEVEL = 6,
    KYVPLOG_NOTICE_LEVEL = 5,
    KYVPLOG_WARN_LEVEL = 4,
    KYVPLOG_ERROR_LEVEL = 3,
    KYVPLOG_CRITICAL_LEVEL = 2,
    KYVPLOG_FATAL_LEVEL = 1
}KYVPLOG_PRIORITY;

KYVP_EXTENSION_API 
KYVP_EXPERIMENTAL_PREFIX
KY_RESULT KYVP_CALLCONV 
/**
 * @warning Experimental API. Do not call this function directly; its signature may change without notice.
 * Use the KYVPLOG_* macros (e.g., KYVPLOG_DEBUG, KYVPLOG_INFO) instead. */
 KYVPExtension_AppLog_V1(KYVPLOG_PRIORITY _log_priority,
                        const char* _pszLoggerName, 
                        const char* _pszFuncName,
                        int _line,
                        KY_PRINTF_PARAM const char* _pszFmt, 
                        ...) KY_PRINTF_ATTR(5, 6) KYVP_EXPERIMENTAL_SUFFIX;


/* Return pointer to basename within path */
static const char* kyvp_path_basename(const char* _path) 
{
    const char* p = strrchr(_path, '/');
    if (!p) p = strrchr(_path, '\\');
    return p ? p + 1 : _path;
}

/* Case-insensitive substring search: returns 1 if hay contains needle */
//static int kyvp_contains_icase_(const char* _hay, const char* _needle) 
//{
//    size_t nl = strlen(_needle);
//    if (nl == 0) return 1;
//    for (const char* p = _hay; *p; ++p) 
//    {
//        size_t i = 0;
//        while (i < nl) 
//        {
//            unsigned char a = (unsigned char)p[i];
//            unsigned char b = (unsigned char)_needle[i];
//            if (!a) break;
//            a = (unsigned char)tolower(a);
//            b = (unsigned char)tolower(b);
//            if (a != b) break;
//            ++i;
//        }
//        if (i == nl) return 1;
//    }
//    return 0;
//}

#if defined(__cplusplus)
    #if __cplusplus >= 201103L
    #define KYVP_TLS thread_local
    #else
    #define KYVP_TLS /* no TLS */
    #endif
#elif defined(_MSC_VER)
    #define KYVP_TLS __declspec(thread)
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
    #define KYVP_TLS _Thread_local
#else
    #define KYVP_TLS /* no TLS */
#endif

static size_t kyvp_strlcpy_(char* _dst, const char* _src, size_t _dsz)
{
    size_t n = 0;
    if (_dsz)
    {
        while (n + 1 < _dsz && _src[n]) { _dst[n] = _src[n]; ++n; }
        _dst[n] = '\0';
    }
    while (_src[n]) ++n; /* return full length of src */
    return n;
}

KY_DISABLE_WARNING_PUSH
KY_DISABLE_WARNING_UNREFERENCED_FUNCTION
KY_DISABLE_WARNING_FUNCTION_NOT_INLINED
static const char* kyvp_current_source_filename(const char* _p_filename)
{
    #if defined(FILENAME_MAX) && FILENAME_MAX > 0
    static KYVP_TLS char buf[FILENAME_MAX];
    #else
    static KYVP_TLS char buf[260];
    #endif
    const char* file = kyvp_path_basename(_p_filename);
    (void)kyvp_strlcpy_(buf, file, sizeof(buf));
    /* Strip extension at the last dot (ignore a leading dot edge case if desired) */
    //#pragma warning(suppress: 4710)
    char* dot = strrchr(buf, '.');
    if (dot /* && dot != buf */) *dot = '\0';
    return buf;
}
KY_DISABLE_WARNING_POP

#define KYVPLOG_FILE (kyvp_current_source_filename(__FILE__))

#ifdef __GNUG__
#define KYVPLOG_FUNC __func__
#else // MSVC
#define KYVPLOG_FUNC __FUNCTION__
#endif

//#define KYVPLOG_ARG_STR(arg) #arg
//#define KYVPLOG_TOSTRING(x) KYVPLOG_ARG_STR(x)
//#define KYVPLOG_LINE(line) "[" KYVPLOG_ARG_STR(line) "]"
//#define KYVPLOG_LOC(file, line) file "[" KYVPLOG_ARG_STR(line) "]"

// The ##__VA_ARGS__ syntax is a GNU extension that removes the comma if no variadic arguments are provided
// Microsoft compiler supports it starting from MSVC 2013
//#define KYVPLOG_TEST(fmt, ...) KYVPExtension_AppLog_V1(KYVP_LOG_DEBUG_LEVEL, KYVPLOG_FUNC, "[%d] " fmt, __LINE__, ##__VA_ARGS__)

#define KYVP_USE_EXPERIMENTAL_APIS
#ifdef KYVP_USE_EXPERIMENTAL_APIS
#define KYVPLOG(_log_priority, _fmt, ...) KYVPExtension_AppLog_V1(_log_priority,\
                                                                  KYVPLOG_FILE,\
                                                                  KYVPLOG_FUNC,\
                                                                  __LINE__,\
                                                                  _fmt,\
                                                                  ##__VA_ARGS__)
#else
#define KYVPLOG(_log_priority, _fmt, ...) 
#endif
#define KYVPLOG_TRACE(_fmt, ...)    KYVPLOG(KYVPLOG_TRACE_LEVEL,    _fmt, ##__VA_ARGS__)
#define KYVPLOG_DEBUG(_fmt, ...)    KYVPLOG(KYVPLOG_DEBUG_LEVEL,    _fmt, ##__VA_ARGS__)
#define KYVPLOG_INFO(_fmt, ...)     KYVPLOG(KYVPLOG_INFO_LEVEL,     _fmt, ##__VA_ARGS__)
#define KYVPLOG_NOTICE(_fmt, ...)   KYVPLOG(KYVPLOG_NOTICE_LEVEL,   _fmt, ##__VA_ARGS__)
#define KYVPLOG_WARN(_fmt, ...)     KYVPLOG(KYVPLOG_WARN_LEVEL,     _fmt, ##__VA_ARGS__)
#define KYVPLOG_ERROR(_fmt, ...)    KYVPLOG(KYVPLOG_ERROR_LEVEL,    _fmt, ##__VA_ARGS__)
#define KYVPLOG_CRITICAL(_fmt, ...) KYVPLOG(KYVPLOG_CRITICAL_LEVEL, _fmt, ##__VA_ARGS__)
#define KYVPLOG_FATAL(_fmt, ...)    KYVPLOG(KYVPLOG_FATAL_LEVEL,    _fmt, ##__VA_ARGS__)


#define KYVPLOG_AND_RETURN_ERROR(_err)\
        {\
            KYVPLOG_ERROR("returning error %d", _err);\
            return _err;\
        }
#define KYVPLOG_AND_RETURN_ERROR_MSG(_err, _fmt, ...) \
        {\
            KYVPLOG_ERROR("returning error %d, " _fmt, _err, ##__VA_ARGS__);\
            return _err;\
        }

#endif  // #ifndef KYVP_LOGGING_H_
