#ifndef KYGENTLEXTENSION_TYPEDEFS_H_
#define KYGENTLEXTENSION_TYPEDEFS_H_

#include "GenTL_v1_5.h"

#include "KYFoundation.h"
#include "KYVPDefines.h"

#define KYGENTLEXTENSION_GC_ERROR_INTERFACE_FW_VERSION_OUTDATED         (GenTL::GC_ERR_CUSTOM_ID - 1)

#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_MAJOR                      (GenTL::TL_INFO_CUSTOM_ID + 1)              // INT32  Software API major version
#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_MINOR                      (GenTL::TL_INFO_CUSTOM_ID + 2)              // INT32  Software API minor version
#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_PATCH                      (GenTL::TL_INFO_CUSTOM_ID + 3)              // INT32  Software API patch version
#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_BUILD                      (GenTL::TL_INFO_CUSTOM_ID + 4)              // INT32  Software API build(revision) version
#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_ALPHA                      (GenTL::TL_INFO_CUSTOM_ID + 5)              // BOOL  True if alpha build
#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_BETA                       (GenTL::TL_INFO_CUSTOM_ID + 6)              // BOOL  True if beta build
#define KYGENTLEXTENSION_TL_INFO_CMD_API_VER_RC                         (GenTL::TL_INFO_CUSTOM_ID + 7)              // BOOL  True if release candidate build

#define KYGENTLEXTENSION_INTERFACE_INFO_CMD_PCI_SLOT                    (GenTL::INTERFACE_INFO_CUSTOM_ID + 1)       // STRING        Info about interface PCI slot
#define KYGENTLEXTENSION_INTERFACE_INFO_CMD_KYVP_PCI_INTERFACE_INFO     (GenTL::INTERFACE_INFO_CUSTOM_ID + 2)       // PTR           Pointer to KYVP_PCI_INTERFACE_INFO struct

#define KYGENTLEXTENSION_DEVICE_INFO_CMD_KYVP_DEVICE_INFO               (GenTL::DEVICE_INFO_CUSTOM_ID + 1)          // PTR           Pointer to KYVP_DEVICE_INFO struct

#define KYGENTLEXTENSION_BUFFER_INFO_CMD_BUFFER_ID                      (GenTL::BUFFER_INFO_CUSTOM_ID + 1)          // UINT64        Buffer ID in buffers queue
#define KYGENTLEXTENSION_BUFFER_INFO_CMD_INSTANT_FPS                    (GenTL::BUFFER_INFO_CUSTOM_ID + 2)          // FLOAT64       Instant FPS counter
#define KYGENTLEXTENSION_BUFFER_INFO_CMD_KYVP_BUFFER_INFO               (GenTL::BUFFER_INFO_CUSTOM_ID + 3)          // PTR           Pointer to KYVP_BUFFER_INFO struct
#define KYGENTLEXTENSION_BUFFER_INFO_CMD_TIMESTAMP_NS_CHRONO            (GenTL::BUFFER_INFO_CUSTOM_ID + 4)          // UINT64        Timestamp the interrupt for this buffer was received (std::chrono::steady_clock), in units of 1 ns (1 000 000 000 ticks per second)
#define KYGENTLEXTENSION_BUFFER_INFO_CMD_TIMESTAMP_NS_HW                ((GenTL::BUFFER_INFO_CUSTOM_ID * 10) + 1)   // UINT64        Timestamp the hw interrupt for this buffer was received (hardware register), in units of 1 ns (1 000 000 000 ticks per second)

#define KYGENTLEXTENSION_EVENT_TYPE_PCIINTERFACE_EVENT                  (GenTL::EVENT_CUSTOM_ID + 1)

#pragma pack(push, 1)

#ifdef __cplusplus
static const int KYGENTLEXTENSION_PCIINTERFACE_EVENT_TEMPERATURE_VERSION = 1;
#else
#define KYGENTLEXTENSION_PCIINTERFACE_EVENT_TEMPERATURE_VERSION 1
#endif
typedef struct _KYGENTLEXTENSION_PCIINTERFACE_EVENT_TEMPERATURE
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYGENTLEXTENSION_PCIINTERFACE_EVENT_TEMPERATURE_VERSION);
    KYVP_PCIINTERFACE_EVENT_TEMPERATURE_THRESHOLD_ID    eTemperatureThresholdId;

}KYGENTLEXTENSION_PCIINTERFACE_EVENT_TEMPERATURE;

#ifdef __cplusplus
static const int KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_VERSION = 1;
#else
#define KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_VERSION 1
#endif
typedef struct _KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_HEARTBEAT
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_HEARTBEAT_VERSION);
    GenTL::DEV_HANDLE                                   deviceHandle;
    KYVP_CXP2_HEARTBEAT                                 heartBeat;

}KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_HEARTBEAT;

#ifdef __cplusplus
static const int KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_EVENT_VERSION = 1;
#else
#define KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_EVENT_VERSION 1
#endif
typedef struct _KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_EVENT
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_EVENT_VERSION);
    GenTL::DEV_HANDLE                                   deviceHandle;
    KYVP_CXP2_EVENT                                     cxp2Event;

}KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_EVENT;

#ifdef __cplusplus
static const int KYGENTLEXTENSION_PCIINTERFACE_GENERIC_EVENT_VERSION = 1;
#else
#define KYGENTLEXTENSION_PCIINTERFACE_GENERIC_EVENT_VERSION 1
#endif
typedef struct _KYGENTLEXTENSION_PCIINTERFACE_GENERIC_EVENT
{
    uint32_t                                            version     KYVP_MEMBER_DEFAULT(KYGENTLEXTENSION_PCIINTERFACE_GENERIC_EVENT_VERSION);
    KYVP_PCIINTERFACE_EVENT_ID                          eEventId;

    union
    {
        KYGENTLEXTENSION_PCIINTERFACE_EVENT_TEMPERATURE     eventTemperature;
        KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_HEARTBEAT  eventCXP2Heartbeat;
        KYGENTLEXTENSION_PCIINTERFACE_EVENT_CXP2_EVENT      eventCXP2Event;
    } data;

}KYGENTLEXTENSION_PCIINTERFACE_GENERIC_EVENT;

#pragma pack(pop)

#endif // #ifndef KYGENTLEXTENSION_TYPEDEFS_H_
