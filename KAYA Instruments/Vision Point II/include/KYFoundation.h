#ifndef KY_FOUNDATION_H_
#define KY_FOUNDATION_H_

#include "KYFoundation_CompilerWarnings.h"
#include "KYFoundation_Types.h"
#include "KYFoundation_Errors.h"

// KY_CALLCONV definition
#ifdef __GNUC__
#define KY_CALLCONV
#ifndef KY_INLINE
#define KY_FORCE_INLINE_GCC __attribute__((always_inline))
#define KY_FORCE_INLINE_MSVC
#define KY_INLINE __inline
#endif //KY_INLINE
#elif defined(_MSC_VER)
#ifndef KY_INLINE
#define KY_FORCE_INLINE_GCC
#define KY_FORCE_INLINE_MSVC __forceinline
#define KY_INLINE
#endif //KY_INLINE

#define KY_CALLCONV __cdecl
#else
#define KY_CALLCONV
#endif

#ifdef __cplusplus
#define KYFOUNDATION_EXTERNAL_C extern "C"
#else
#define KYFOUNDATION_EXTERNAL_C
#endif

#ifdef _MSC_VER
#ifdef KY_FOUNDATION_EXPORTS
#define KYFOUNDATION_EXTERNAL_DLL __declspec(dllexport)
#else
#define KYFOUNDATION_EXTERNAL_DLL __declspec(dllimport)
#endif
#else
#define KYFOUNDATION_EXTERNAL_DLL
#endif

#define KYFOUNDATION_API          KYFOUNDATION_EXTERNAL_C KYFOUNDATION_EXTERNAL_DLL


#if defined(_MSC_VER)
#include <sal.h>
// Use on format string parameter
#define KY_PRINTF_PARAM _Printf_format_string_
// Function-level attribute is not supported in MSVC, so define as empty
#define KY_PRINTF_ATTR(_fmt_index, _args_index)
#else
  // MSVC SAL annotations not recognized by GCC/Clang
#define KY_PRINTF_PARAM
// Use as function attribute after parameter list
#define KY_PRINTF_ATTR(_fmt_index, _args_index) __attribute__((format(printf, _fmt_index, _args_index)))
#endif

//////////////////////////////////////////////////////////////////////////////////
/// MACROS FOR KY_RESULT INTERPRETATION
//////////////////////////////////////////////////////////////////////////////////

#if defined(__cplusplus)
inline bool KY_RESULT_SUCCEEDED(KY_RESULT r)
{
    return ( 0 == ((r.uResult >> 31) & 0x01) );
}
#else
#define KY_RESULT_SUCCEEDED(result) (0 == ((result.uResult >> 31) & 0x01))
#endif
#define KY_RESULT_FAILED(result)            (!KY_RESULT_SUCCEEDED(result))

////////////////////////////////////////////////////////////////////////////////////////
/// ERROR HANDLING API FUNCTIONS
////////////////////////////////////////////////////////////////////////////////////////

KYFOUNDATION_API const char* KY_CALLCONV KYFoundation_What(KY_RESULT _result);
KYFOUNDATION_API uint32_t KY_CALLCONV KYFoundation_GetErrorCode(KY_RESULT _result);
KYFOUNDATION_API uint32_t KY_CALLCONV KYFoundation_GetSeverity(KY_RESULT _result);

#endif  // #ifndef KY_FOUNDATION_H_
