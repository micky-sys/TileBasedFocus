#ifndef KYVP_EXPERIMENTAL_H_
#define KYVP_EXPERIMENTAL_H_

/* ---- experimental markers ---- */
#if defined(__clang__)
#if __has_attribute(diagnose_if)
#define KYVP_EXPERIMENTAL_PREFIX
#define KYVP_EXPERIMENTAL_SUFFIX __attribute__((diagnose_if(1, "experimental API: subject to change", "warning")))
#else
#define KYVP_EXPERIMENTAL_PREFIX
#define KYVP_EXPERIMENTAL_SUFFIX
#endif
#elif defined(__GNUC__)
#if __GNUC__ >= 9
#define KYVP_EXPERIMENTAL_PREFIX
#define KYVP_EXPERIMENTAL_SUFFIX __attribute__((warning("experimental API: subject to change")))
#else
#define KYVP_EXPERIMENTAL_PREFIX
#define KYVP_EXPERIMENTAL_SUFFIX
#endif
#elif defined(_MSC_VER)
/* MSVC requires declspec before the declarator */
/* MSVC has no "warning on use" attribute; so we *misuse* deprecation: */
#if defined(KYVP_USE_EXPERIMENTAL_APIS)
    #pragma message("KYVP: Experimental APIs are present; semantics may change. #undefine KYVP_USE_EXPERIMENTAL_APIS to get per-use warnings.")
    #define KYVP_EXPERIMENTAL_PREFIX
#else
    #define KYVP_EXPERIMENTAL_PREFIX __declspec(deprecated("experimental API: subject to change"))
#endif
#define KYVP_EXPERIMENTAL_SUFFIX
#else
#define KYVP_EXPERIMENTAL_PREFIX
#define KYVP_EXPERIMENTAL_SUFFIX
#endif














#endif  // #ifndef KYVP_EXPERIMENTAL_H_
