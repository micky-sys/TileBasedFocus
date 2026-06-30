#ifndef KYFG_CPP_LIB_H_
#define KYFG_CPP_LIB_H_

#include "KYFGLib.h"

#include <string>

#ifdef __GNUG__
#include "ky_lib_defines.h" // #if(GCC_VERSION <= 40407) #define nullptr 0
#endif

// C++ only API:
#ifdef __cplusplus

#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable: 4190)
#endif // #ifdef _MSC_VER

enum class NodeDescriptorType
{
    Invalid = 0,
    NewNode = 1,
    NewEnumEntry = 2,
    UpdateNode = 3
};

enum class ParameterInterfaceType
{
    intfIValue,
    intfIBase,
    intfIInteger,
    intfIBoolean,
    intfICommand,
    intfIFloat,
    intfIString,
    intfIRegister,
    intfICategory,
    intfIEnumeration,
    intfIEnumEntry,
    intfIPort, 

    intfIUnknown
};

static const uint32_t AllParameterInterfaceTypesMask =
    ( uint32_t(1) << int(ParameterInterfaceType::intfIValue)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIBase)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIInteger)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIBoolean)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfICommand)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIFloat)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIString)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIRegister)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfICategory)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIEnumeration)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIEnumEntry)) |
    ( uint32_t(1) << int(ParameterInterfaceType::intfIPort));

static const uint32_t ReadKYFGLibXmlJsonMask = 4096;

enum class ParameterRepresentation
{
    Linear,
    Logarithmic,
    Boolean,
    PureNumber,
    HexNumber,
    IPV4Address,
    MACAddress,
    _UndefinedRepresentation
};

enum class ParameterVisibility
{
    Unknown		= 0xFF,
	Zero        = 0x00,
    Beginner	= 0x01,
    Expert		= 0x02,
	Guru        = 0x03,
	Developer   = 0x04,
	Invisible	= 0x05
};

// Gen<i>Cam nodes description
struct NodeDescriptor
{
	NodeDescriptor()
    {}
    // Initially descriptor should be invalid to avoid noise effects
    // callback issuers are responsible to change descriptor's type appropriately
    // nullify all pointers
    NodeDescriptorType descriptorType = NodeDescriptorType::Invalid;
    ParameterInterfaceType interfaceType = ParameterInterfaceType::intfIUnknown;
    const char* paramName = nullptr;                      // Machine name of parameter. This name should be used as argument ‘paramName’ for KYFG_SetGrabberValueXXX() and KYFG_GetGrabberValueXXX() calls
    const char* paramDisplayName = nullptr;               // Human readable name of parameter, used in GUI
    const char* toolTip = nullptr;
    bool isAvailable = false;                   // `true’ if parameter is currently available, 'false' if any attempt to access this parameter will result in an error (GUI should not show it)
    bool isWritable = false;                    /* `true’ if parameter is writable, i.e. KYFG_SetGrabberValueXXX() can be called for it;
                                               ‘false’ otherwise – attempt to set it will result in error FGSTATUS_PARAMETER_NOT_WRITABLE*/
    bool isReadable = false;
    ParameterRepresentation representation = ParameterRepresentation::_UndefinedRepresentation; // Indicates type of GUI element suggested for this parameter representation
    ParameterVisibility visibility = ParameterVisibility::Unknown;  // Visibility level. Used in GUI for filtering set of visible parameters
    int64_t minIntValue = LLONG_MIN;            // Minimum possible / allowed value in case parameter has ‘intfIInteger’ interfaceType
    int64_t maxIntValue = LLONG_MAX;            // Maximum possible / allowed value in case parameter has ‘intfIInteger’ interfaceType, length of register in case parameter has ‘intfIRegister’ interfaceType
    int64_t incIntValue = 1;                   // Single increment / decrement step in case parameter has ‘intfIInteger’ interfaceType
                                                // default increment to 1 unless noIncrement != ptrInteger->GetIncMode() && ptrInteger->GetInc() doesn't throw
    int64_t curIntValue = 0;                    // Current value in case parameter has ‘intfIInteger’ interfaceType
    double minFloatValue = LDBL_MIN;            // Minimum possible / allowed value in case parameter has ‘intfIFloat’ interfaceType
    double maxFloatValue = LDBL_MAX;            // Maximum possible / allowed value in case parameter has ‘intfIFloat’ interfaceType
    double incFloatValue = 1;                  // Single increment / decrement step in case parameter has ‘intfIFloat’ interfaceType
                                                // default increment to 1 unless ptrFloat->HasInc() && ptrFloat->GetInc() doesn't throw
    int64_t  floatDisplayPrecision = 2;         // Decimal precision in case parameter has ‘intfIFloat’ interfaceType
    double curFloatValue = 0;                   // Current value in case parameter has ‘intfIFloat’ interfaceType
    bool   curBoolValue = false;                // Current value in case parameter has ‘intfIBoolean’ interfaceType
    const char* curStringValue = nullptr;                 // Current value in case parameter has ‘intfIString’ or ‘intfIRegister’ interfaceType
    
    bool isSelector = false;                    // `true’ if this node acts as selector for other nodes, ‘false’ otherwise
    const char* selectorName = nullptr;                   // name of another node that acts as selector for this node, NULL if this node is not selected

    const NodeDescriptor* pParentNode = nullptr;          // Pointer to parent node in nodes hierarchy

    int64_t curSelectorValue = -1;
    const char* description = nullptr;

    // Helper methods
    //std::string ValueAsString() const;

    // Debugging:
    bool bTracing = false; // parameter is being traced

	const char* physicalUnitName = nullptr;								// Human readable pysical unit name
};

// Grabber and Camera parameters definition as specified in loaded xml
typedef void (KYFG_CALLCONV *ParameterCallback)(void* userContext, const NodeDescriptor* pNodeDescriptor, int grouppingLevel);

// Grabber:
KAYA_API FGSTATUS KYFG_SetGrabberConfigurationParameterCallback(FGHANDLE grabberHandle, ParameterCallback userFunc, void* userContext);
KAYA_API FGSTATUS KYFG_GetGrabberConfigurationParameterDefinitions(FGHANDLE grabberHandle);


// Camera:
KAYA_API FGSTATUS KYFG_SetCameraConfigurationParameterCallback(CAMHANDLE camHandle, ParameterCallback userFunc, void* userContext);
KAYA_API FGSTATUS KYFG_GetCameraConfigurationParameterDefinitions(CAMHANDLE camHandle);

#ifdef _MSC_VER
#pragma warning(pop)
#endif // #ifdef _MSC_VER

#endif // #ifdef __cplusplus
// End of C++ only API


#endif  // #ifndef KYFG_CPP_LIB_H_
