/* SPDX-License-Identifier: MIT
 *
 * canopen-interop device application for CANopenLinux's canopend
 * (CO_USE_APPLICATION, CO_application.h). The CANopen work is done by
 * CANopenNode: this file is the device's application, as a product built on
 * CANopenNode would have one. It
 *
 *   - sets the producer heartbeat time 0x1017 at power-on (COI_HEARTBEAT_MS),
 *   - backs the DOMAIN 0x2005 with a 4096-byte buffer (CANopenNode keeps no
 *     storage for domains: the application provides read/write functions),
 *   - increments the counter 0x2002:00 every COI_TICK_MS and requests the
 *     event-driven TPDOs that map it (OD_requestTPDO),
 *   - reports one error condition with CO_errorReport() after
 *     COI_EMCY_AFTER_MS (COI_EMCY = "<code hex>:<errorBit hex>:<infoCode hex>"),
 *   - prints the values of COI_DUMP ("2000:00,2004:00", hex) as the Object
 *     Dictionary holds them when the program ends,
 *   - sets the serial number 0x1018:04 at power-on (COI_SERIAL), the LSS
 *     address of the LSS slave (CiA 305),
 *   - accepts every bit rate of the CiA 301 table for the LSS slave's
 *     "configure bit timing" and reports "activate bit timing" (CANopenLinux
 *     registers no such callbacks, so CANopenNode would ignore both; vcan has
 *     no bit rate to switch).
 *
 * Lines for the adapter (adapter.py) on stdout: "COI READY" once the node
 * left the initialising state (boot-up sent) or runs as an unconfigured LSS
 * slave, "COI NODEID <id>" at every communication reset (255: unconfigured),
 * "COI BITRATE <kbit/s> <delay ms>", "COI DUMP <idx>:<sub> <hex>" or
 * "COI DUMP <idx>:<sub> ERR <ODR>", "COI END".
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>

#include "CO_application.h"
#include "OD.h"

static uint32_t tickUs, tickAccUs;
static uint32_t emcyAfterUs, runUs;
static int emcyPending;
static uint16_t emcyCode;
static uint8_t emcyBit;
static uint32_t emcyInfo;
static int ready;
static uint16_t pendingKbps;

/* DOMAIN 0x2005 */
static uint8_t domainBuf[4096];
static OD_size_t domainLen;
static OD_extension_t domainExt;
static OD_extension_t counterExt;

static unsigned long
envul(const char* name, unsigned long dflt) {
    const char* v = getenv(name);
    return (v != NULL && *v != '\0') ? strtoul(v, NULL, 0) : dflt;
}

static ODR_t
domainRead(OD_stream_t* stream, void* buf, OD_size_t count, OD_size_t* countRead) {
    if (stream->dataOffset == 0U) {
        stream->dataLength = domainLen;
    }
    OD_size_t left = domainLen - stream->dataOffset;
    OD_size_t n = left < count ? left : count;
    memcpy(buf, domainBuf + stream->dataOffset, n);
    *countRead = n;
    if (n < left) {
        stream->dataOffset += n;
        return ODR_PARTIAL;
    }
    stream->dataOffset = 0U;
    return ODR_OK;
}

static ODR_t
domainWrite(OD_stream_t* stream, const void* buf, OD_size_t count, OD_size_t* countWritten) {
    if (stream->dataOffset + count > sizeof(domainBuf)) {
        return ODR_DATA_LONG;
    }
    memcpy(domainBuf + stream->dataOffset, buf, count);
    stream->dataOffset += count;
    *countWritten = count;
    /* dataLength is the indicated or (at the end) the transferred size */
    if (stream->dataLength == 0U || stream->dataOffset < stream->dataLength) {
        return ODR_PARTIAL;
    }
    domainLen = stream->dataOffset;
    stream->dataOffset = 0U;
    return ODR_OK;
}

CO_ReturnError_t
app_programStart(uint16_t* bitRate, uint8_t* nodeId, uint32_t* errInfo) {
    (void)bitRate;
    (void)nodeId;
    OD_entry_t* e;

    if (getenv("COI_SERIAL") != NULL) {
        e = OD_find(OD, 0x1018);
        if (e == NULL || OD_set_u32(e, 4, (uint32_t)envul("COI_SERIAL", 0), true) != ODR_OK) {
            *errInfo = 0x1018;
            return CO_ERROR_OD_PARAMETERS;
        }
    }
    if (getenv("COI_HEARTBEAT_MS") != NULL) {
        e = OD_find(OD, 0x1017);
        if (e == NULL || OD_set_u16(e, 0, (uint16_t)envul("COI_HEARTBEAT_MS", 0), true) != ODR_OK) {
            *errInfo = 0x1017;
            return CO_ERROR_OD_PARAMETERS;
        }
    }
    e = OD_find(OD, 0x2005);
    if (e != NULL) {
        domainExt.object = NULL;
        domainExt.read = domainRead;
        domainExt.write = domainWrite;
        OD_extension_init(e, &domainExt);
    }
    e = OD_find(OD, 0x2002);
    if (e != NULL) {
        /* the default functions; the extension carries the TPDO request flags */
        counterExt.object = NULL;
        counterExt.read = OD_readOriginal;
        counterExt.write = OD_writeOriginal;
        OD_extension_init(e, &counterExt);
    }
    tickUs = (uint32_t)envul("COI_TICK_MS", 0) * 1000U;
    if (getenv("COI_EMCY") != NULL) {
        unsigned code, bit;
        unsigned long info;
        if (sscanf(getenv("COI_EMCY"), "%x:%x:%lx", &code, &bit, &info) != 3) {
            *errInfo = 0x1014;
            return CO_ERROR_ILLEGAL_ARGUMENT;
        }
        emcyCode = (uint16_t)code;
        emcyBit = (uint8_t)bit;
        emcyInfo = (uint32_t)info;
        emcyAfterUs = (uint32_t)envul("COI_EMCY_AFTER_MS", 0) * 1000U;
        emcyPending = 1;
    }
    return CO_ERROR_NO;
}

/* LSS slave: every bit rate of the CiA 301 table is accepted (vcan). */
static bool_t
lssCheckBitRate(void* object, uint16_t bitRate) {
    (void)object;
    pendingKbps = bitRate;
    return true;
}

static void
lssActivateBitRate(void* object, uint16_t delay) {
    (void)object;
    printf("COI BITRATE %u %u\n", (unsigned)pendingKbps, (unsigned)delay);
    fflush(stdout);
}

void
app_communicationReset(CO_t* co) {
    printf("COI NODEID %u\n", co->nodeIdUnconfigured ? 255U : (unsigned)co->NMT->nodeId);
    fflush(stdout);
    if (co->LSSslave != NULL) {
        CO_LSSslave_initCkBitRateCall(co->LSSslave, NULL, lssCheckBitRate);
        CO_LSSslave_initActBitRateCall(co->LSSslave, NULL, lssActivateBitRate);
    }
}

void
app_programEnd(void) {
    const char* list = getenv("COI_DUMP");
    if (list != NULL) {
        char* copy = strdup(list);
        for (char* tok = strtok(copy, ","); tok != NULL; tok = strtok(NULL, ",")) {
            unsigned idx, sub;
            if (sscanf(tok, "%x:%x", &idx, &sub) != 2) {
                continue;
            }
            OD_IO_t io;
            OD_entry_t* e = OD_find(OD, (uint16_t)idx);
            ODR_t r = e == NULL ? ODR_IDX_NOT_EXIST : OD_getSub(e, (uint8_t)sub, &io, false);
            static uint8_t buf[8192];
            OD_size_t total = 0;
            while (r == ODR_OK) {
                OD_size_t n = 0;
                r = io.read(&io.stream, buf + total, (OD_size_t)(sizeof(buf) - total), &n);
                total += n;
                if (r == ODR_PARTIAL) {
                    r = ODR_OK;
                    continue;
                }
                break;
            }
            if (r != ODR_OK) {
                printf("COI DUMP %04X:%02X ERR %d\n", idx, sub, (int)r);
                continue;
            }
            printf("COI DUMP %04X:%02X ", idx, sub);
            for (OD_size_t i = 0; i < total; i++) {
                printf("%02x", buf[i]);
            }
            printf("\n");
        }
        free(copy);
    }
    printf("COI END\n");
    fflush(stdout);
}

void
app_programAsync(CO_t* co, uint32_t timer1usDiff) {
    if (!ready && (co->nodeIdUnconfigured || CO_NMT_getInternalState(co->NMT) != CO_NMT_INITIALIZING)) {
        ready = 1;
        printf("COI READY\n");
        fflush(stdout);
    }
    runUs += timer1usDiff;
    if (emcyPending && ready && runUs >= emcyAfterUs) {
        emcyPending = 0;
        CO_errorReport(co->em, emcyBit, emcyCode, emcyInfo);
    }
    /* canopend is built with CO_SINGLE_THREAD: app_programRt() is not
     * called, the counter ticks here (main loop interval 10 ms). */
    if (tickUs == 0U) {
        return;
    }
    tickAccUs += timer1usDiff;
    while (tickAccUs >= tickUs) {
        tickAccUs -= tickUs;
        OD_entry_t* e = OD_find(OD, 0x2002);
        uint32_t v = 0;
        CO_LOCK_OD(co->CANmodule);
        if (OD_get_u32(e, 0, &v, true) == ODR_OK) {
            OD_set_u32(e, 0, v + 1U, true);
            OD_requestTPDO(e, 0);
        }
        CO_UNLOCK_OD(co->CANmodule);
    }
}

void
app_programRt(CO_t* co, uint32_t timer1usDiff) {
    (void)co;
    (void)timer1usDiff;
}
