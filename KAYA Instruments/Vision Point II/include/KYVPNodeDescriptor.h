#ifndef KYVP_NODE_DESCRIPTOR_H
#define KYVP_NODE_DESCRIPTOR_H

#include "KYVPDefines.h"
#include "KYFoundation_Types.h"

/**  
 * @page KYVP_NodeDescriptor KYVP Node Descriptor
 * @section KYVP_NodeDescriptorType KYVP_NodeDescriptorType 
 * | Enum Field                                                 | Value     | Description       |
 * | -----------------------                                    | --------  | -----------       |
 * | KYVP_NodeDescriptorType_Invalid                            | 0x1       |                   |
 * | KYVP_NodeDescriptorType_NewNode                            | 0x2       |                   |
 * | KYVP_NodeDescriptorType_NewEnumEntry                       | 0x3       |                   |
 * | KYVP_NodeDescriptorType_UpdateNode                         | 0x4       |                   |
 */
typedef enum _KYVP_NodeDescriptorType
{
    KYVP_NodeDescriptorType_Invalid,
    KYVP_NodeDescriptorType_NewNode,
    KYVP_NodeDescriptorType_NewEnumEntry,
    KYVP_NodeDescriptorType_UpdateNode
}KYVP_NodeDescriptorType;

/**
 * @page KYVP_NodeDescriptor KYVP Node Descriptor
 * @section KYVP_ParameterInterfaceType KYVP_ParameterInterfaceType
 * | Enum Field                                                 | Value     | Description       |
 * | -----------------------                                    | --------  | -----------       |
 * | KYVP_ParameterInterfaceType_IValue                         | 0x0       |                   |
 * | KYVP_ParameterInterfaceType_IBase                          | 0x1       |                   |
 * | KYVP_ParameterInterfaceType_IInteger                       | 0x2       |                   |
 * | KYVP_ParameterInterfaceType_IBoolean                       | 0x3       |                   |
 * | KYVP_ParameterInterfaceType_ICommand                       | 0x4       |                   |
 * | KYVP_ParameterInterfaceType_IFloat                         | 0x5       |                   |
 * | KYVP_ParameterInterfaceType_IString                        | 0x6       |                   |
 * | KYVP_ParameterInterfaceType_IRegister                      | 0x7       |                   |
 * | KYVP_ParameterInterfaceType_ICategory                      | 0x8       |                   |
 * | KYVP_ParameterInterfaceType_IEnumeration                   | 0x9       |                   |
 * | KYVP_ParameterInterfaceType_IEnumEntry                     | 0xA       |                   |
 * | KYVP_ParameterInterfaceType_IPort                          | 0xB       |                   |
 * | KYVP_ParameterInterfaceType_IUnknown                       | 0xC       |                   |
 */

typedef enum _KYVP_ParameterInterfaceType
{
    KYVP_ParameterInterfaceType_IValue,
    KYVP_ParameterInterfaceType_IBase,
    KYVP_ParameterInterfaceType_IInteger,
    KYVP_ParameterInterfaceType_IBoolean,
    KYVP_ParameterInterfaceType_ICommand,
    KYVP_ParameterInterfaceType_IFloat,
    KYVP_ParameterInterfaceType_IString,
    KYVP_ParameterInterfaceType_IRegister,
    KYVP_ParameterInterfaceType_ICategory,
    KYVP_ParameterInterfaceType_IEnumeration,
    KYVP_ParameterInterfaceType_IEnumEntry,
    KYVP_ParameterInterfaceType_IPort,
    KYVP_ParameterInterfaceType_IUnknown
}KYVP_ParameterInterfaceType;

/**
 * @page KYVP_NodeDescriptor KYVP Node Descriptor
 * @section KYVP_ParameterRepresentation KYVP_ParameterRepresentation
 * | Enum Field                                                 | Value     | Description       |
 * | -----------------------                                    | --------  | -----------       |
 * | KYVP_ParameterRepresentation_Linear                        | 0x0       |                   |
 * | KYVP_ParameterRepresentation_Logarithmic                   | 0x1       |                   |
 * | KYVP_ParameterRepresentation_Boolean                       | 0x2       |                   |
 * | KYVP_ParameterRepresentation_PureNumber                    | 0x3       |                   |
 * | KYVP_ParameterRepresentation_HexNumber                     | 0x4       |                   |
 * | KYVP_ParameterRepresentation_IPV4Address                   | 0x5       |                   |
 * | KYVP_ParameterRepresentation_MACAddress                    | 0x6       |                   |
 * | KYVP_ParameterRepresentation_UndefinedRepresentation       | 0x7       |                   |
 */

typedef enum _KYVP_ParameterRepresentation
{
    KYVP_ParameterRepresentation_Linear,
    KYVP_ParameterRepresentation_Logarithmic,
    KYVP_ParameterRepresentation_Boolean,
    KYVP_ParameterRepresentation_PureNumber,
    KYVP_ParameterRepresentation_HexNumber,
    KYVP_ParameterRepresentation_IPV4Address,
    KYVP_ParameterRepresentation_MACAddress,
    KYVP_ParameterRepresentation_UndefinedRepresentation
}KYVP_ParameterRepresentation;

/**
 * @page KYVP_NodeDescriptor KYVP Node Descriptor
 * @section KYVP_ParameterVisibility KYVP_ParameterVisibility
 * | Enum Field                                                 | Value     | Description                   |
 * | -----------------------                                    | --------  | -----------                   |
 * | KYVP_ParameterVisibility_Beginner                          | 0x0       | Always visible                |
 * | KYVP_ParameterVisibility_Expert                            | 0x1       | Visible for experts or Gurus  |
 * | KYVP_ParameterVisibility_Guru                              | 0x2       | Visible for Gurus             |
 * | KYVP_ParameterVisibility_Invisible                         | 0x3       | Not Visible                   |
 * | KYVP_ParameterVisibility_Undefined                         | 0x99      | Undefined                     |
 * | KYVP_ParameterVisibility_Developer                         | 0x200     | KAYA Extension                |
 */

typedef enum _KYVP_ParameterVisibility
{
    KYVP_ParameterVisibility_Beginner = 0,      //!< Always visible
    KYVP_ParameterVisibility_Expert = 1,        //!< Visible for experts or Gurus
    KYVP_ParameterVisibility_Guru = 2,          //!< Visible for Gurus
    KYVP_ParameterVisibility_Invisible = 3,     //!< Not Visible
    KYVP_ParameterVisibility_Undefined = 99,    //!< Undefined

    KYVP_ParameterVisibility_Developer = 200,   //!< KAYA Extension


}KYVP_ParameterVisibility;

/**
 * @page KYVP_NodeDescriptor KYVP Node Descriptor
 * @section KYVP_NodeDescriptor KYVP_NodeDescriptor
 * | Structure Field                                | Type                                              | Description                                           | Remarks   |
 * | -------------                                  | -----                                             | -----------                                           | -------   |
 * | m_nVersion                                     | int                                               |                                                       |           |
 * | m_eDescriptorType                              | @ref KYVP_NodeDescriptorType                      |                                                       |           |
 * | m_eInterfaceType                               | @ref KYVP_ParameterInterfaceType                  |                                                       |           |
 * | m_szParamName                                  | const char* const                                 |                                                       |           |
 * | m_szParamDisplayName                           | const char* const                                 |                                                       |           |
 * | m_szToolTip                                    | const char* const                                 |                                                       |           |
 * | m_bIsAvailable                                 | @ref KY_BOOL                                      |                                                       |           |
 * | m_bIsWritable                                  | @ref KY_BOOL                                      |                                                       |           |
 * | m_bIsReadable                                  | @ref KY_BOOL                                      |                                                       |           |
 * | m_bIsSelector                                  | @ref KY_BOOL                                      |                                                       |           |
 * | m_eRepresentation                              | @ref KYVP_ParameterRepresentation                 |                                                       |           |
 * | m_eVisibility                                  | @ref KYVP_ParameterVisibility                     |                                                       |           |
 * | m_nMinIntValue                                 | int64_t                                           |                                                       |           |
 * | m_nMaxIntValue                                 | int64_t                                           |                                                       |           |
 * | m_nIncIntValue                                 | int64_t                                           |                                                       |           |
 * | m_nCurIntValue                                 | int64_t                                           |                                                       |           |
 * | m_nIndent                                      | int64_t                                           |                                                       |           |
 * | m_fMinFloatValue                               | double                                            |                                                       |           |
 * | m_fMaxFloatValue                               | double                                            |                                                       |           |
 * | m_fIncFloatValue                               | double                                            |                                                       |           |
 * | m_nFloatDisplayPrecision                       | int64_t                                           |                                                       |           |
 * | m_fCurFloatValue                               | double                                            |                                                       |           |
 * | m_bCurBoolValue                                | @ref KY_BOOL                                      |                                                       |           |
 * | m_szCurStringValue;                            | const char*                                       |                                                       |           |
 * | m_pParentNode;                                 | const struct @ref KYVP_NodeDescriptor*            |                                                       |           |
 * | m_szSelectorName;                              | const char* const                                 |                                                       |           |
 * | m_nCurSelectorValue;                           | int64_t                                           |                                                       |           |
 * | m_szDescription;                               | const char* const                                 |                                                       |           |
 * | m_bTracing;                                    | @ref KY_BOOL                                      |                                                       |           |
 * | m_szPhysicalUnitName                           | const char* const                                 |                                                       |           |
 */

 /**
  * @page KYVP_NodeDescriptor KYVP Node Descriptor
  * @section KYVP_ParameterAttributeType KYVP_ParameterAttributeType
  * | Enum Field                                                 | Value     | Description                   |
  * | -----------------------                                    | --------  | -----------                   |
  * | KYVP_ParameterAttributeType_Invalidator                    | 0x0       |                               |
  */

typedef enum _KYVP_ParameterAttributeType
{
    KYVP_ParameterAttributeType_Invalidator = 0,


    KYVP_ParameterAttributeType_Invalid = 0xFF
}KYVP_ParameterAttributeType;

#if defined(_MSC_VER)
#  pragma warning(push)
#  pragma warning(disable:4514) /* unreferenced inline function has been removed */
#endif

#pragma pack(push, 1)
typedef struct _KYVP_NodeDescriptor
{
#ifdef __cplusplus
    _KYVP_NodeDescriptor(const char* _szParamName,
        const char* _szParamDisplayName,
        const char* _szToolTip,
        const char* _szDescription,
        const char* _szSelectorName,
        const char* _szPhysicalUnitName,
        const KY_BOOL& _bIsImplemented)
        : m_szParamName(_szParamName)
        , m_szParamDisplayName(_szParamDisplayName)
        , m_szToolTip(_szToolTip)
        , m_szDescription(_szDescription)
        , m_szPhysicalUnitName(_szPhysicalUnitName)
        , m_szSelectorName(_szSelectorName)
        , m_bIsImplemented(_bIsImplemented)
    {

    }

    _KYVP_NodeDescriptor(_KYVP_NodeDescriptor&&)                      = default;
    _KYVP_NodeDescriptor& operator=(_KYVP_NodeDescriptor&&)           = delete;

    _KYVP_NodeDescriptor(const _KYVP_NodeDescriptor&)                 = delete;
    _KYVP_NodeDescriptor& operator=(const _KYVP_NodeDescriptor&)      = delete;

#endif

    int                                 m_nVersion                          KYVP_MEMBER_DEFAULT(0);

    const char*                         m_szParamName                       KYVP_MEMBER_DEFAULT(nullptr);
    const char*                         m_szParamDisplayName                KYVP_MEMBER_DEFAULT(nullptr);
    const char*                         m_szToolTip                         KYVP_MEMBER_DEFAULT(nullptr);
    const char*                         m_szDescription                     KYVP_MEMBER_DEFAULT(nullptr);
    const char*                         m_szPhysicalUnitName                KYVP_MEMBER_DEFAULT(nullptr);

    KYVP_NodeDescriptorType             m_eDescriptorType                   KYVP_MEMBER_DEFAULT(KYVP_NodeDescriptorType_Invalid);
    KYVP_ParameterInterfaceType         m_eInterfaceType                    KYVP_MEMBER_DEFAULT(KYVP_ParameterInterfaceType_IUnknown);
    KYVP_ParameterRepresentation        m_eRepresentation                   KYVP_MEMBER_DEFAULT(KYVP_ParameterRepresentation_UndefinedRepresentation);
    KYVP_ParameterVisibility            m_eVisibility                       KYVP_MEMBER_DEFAULT(KYVP_ParameterVisibility_Beginner);

    int64_t                             m_nLogicalIndex                     KYVP_MEMBER_DEFAULT(0); // Used parameter type is "KYVP_ParameterInterfaceType_IEnumEntry". Keeps index in the corresponding Combo

    const struct _KYVP_NodeDescriptor*  m_pParentNode                       KYVP_MEMBER_DEFAULT(nullptr);

    KY_BOOL                             m_bIsSelector                       KYVP_MEMBER_DEFAULT(KY_FALSE);
    const char*                         m_szSelectorName                    KYVP_MEMBER_DEFAULT(nullptr);
    int64_t                             m_nCurSelectorValue                 KYVP_MEMBER_DEFAULT(0);

    const KY_BOOL                       m_bIsImplemented                    KYVP_MEMBER_DEFAULT(KY_FALSE);
    KY_BOOL                             m_bIsAvailable                      KYVP_MEMBER_DEFAULT(KY_TRUE);
    KY_BOOL                             m_bIsWritable                       KYVP_MEMBER_DEFAULT(KY_TRUE);
    KY_BOOL                             m_bIsReadable                       KYVP_MEMBER_DEFAULT(KY_TRUE);

    int64_t                             m_nMinIntValue                      KYVP_MEMBER_DEFAULT(INT64_MAX);
    int64_t                             m_nMaxIntValue                      KYVP_MEMBER_DEFAULT(INT64_MIN);
    int64_t                             m_nIncIntValue                      KYVP_MEMBER_DEFAULT(1);
    int64_t                             m_nCurIntValue                      KYVP_MEMBER_DEFAULT(0);

    double                              m_fMinFloatValue                    KYVP_MEMBER_DEFAULT(DBL_MIN);
    double                              m_fMaxFloatValue                    KYVP_MEMBER_DEFAULT(DBL_MAX);
    double                              m_fIncFloatValue                    KYVP_MEMBER_DEFAULT(1.);
    int64_t                             m_nFloatDisplayPrecision            KYVP_MEMBER_DEFAULT(1);
    double                              m_fCurFloatValue                    KYVP_MEMBER_DEFAULT(0);

    KY_BOOL                             m_bCurBoolValue                     KYVP_MEMBER_DEFAULT(KY_FALSE);

    const char*                         m_szCurStringValue                  KYVP_MEMBER_DEFAULT(nullptr);

    KY_BOOL                             m_bTracing                          KYVP_MEMBER_DEFAULT(KY_FALSE);

} KYVP_NodeDescriptor;
#pragma pack(pop)

#if defined(_MSC_VER)
#  pragma warning(pop)
#endif

typedef void (*PARAMETER_CALLBACK_FUNC)(const KYVP_NodeDescriptor* _pNodeDescriptor, void* _pContext);

#endif //KYVP_NODE_DESCRIPTOR_H
