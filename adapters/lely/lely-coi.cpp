// SPDX-License-Identifier: MIT
//
// lely-coi: the canopen-interop helper on Lely CANopen (lely-core). Every
// CANopen operation is done by lely-core: liblely-coapp (BasicSlave,
// AsyncMaster, Sdo) and, where coapp has no API for it, liblely-co (the
// NMT command request and Receive-PDO services of the master node).
// adapter.py turns the lines this program prints into the JSON documents of
// docs/CONTAINER_CONTRACT.md.
//
//   lely-coi device IFACE NODE DCF DURATION_MS TICK_MS EMCY EMCY_AFTER_MS DUMP
//       EMCY = EEC:ER:HEX or -, DUMP = IDX:SUB,... or -
//   lely-coi upload IFACE MASTER_DCF NODE IDX SUB BLOCK TIMEOUT_MS
//   lely-coi download IFACE MASTER_DCF NODE IDX SUB BLOCK TIMEOUT_MS HEX
//   lely-coi nmt IFACE MASTER_DCF NODE CS
//   lely-coi listen IFACE MASTER_DCF DURATION_MS
//   lely-coi pdo-receive IFACE MASTER_DCF NODE TPDO DURATION_MS
//
// Output lines: READY, RESULT ..., ERROR <abort code hex> <text>,
// BOOTUP <node>, STATE <node> <state>, EMCY <node> <eec> <er> <hex>,
// PDO <cob-id> <idx>:<sub>=<hex> ..., DUMP <idx>:<sub> <hex>|ERR.

#include <lely/can/net.hpp>
#include <lely/co/dev.hpp>
#include <lely/co/nmt.hpp>
#include <lely/co/obj.hpp>
#include <lely/co/rpdo.hpp>
#include <lely/co/sdo.h>
#include <lely/co/val.hpp>
#include <lely/coapp/master.hpp>
#include <lely/coapp/sdo.hpp>
#include <lely/coapp/slave.hpp>
#include <lely/ev/loop.hpp>
#include <lely/io2/linux/can.hpp>
#include <lely/io2/posix/poll.hpp>
#include <lely/io2/sys/io.hpp>
#include <lely/io2/sys/sigset.hpp>
#include <lely/io2/sys/timer.hpp>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

using namespace lely;
using namespace std::chrono;

namespace {

// coapp hands out the C structs; the C API takes the C++ wrapper types of
// lely's headers (layout-compatible incomplete types).
co_dev_t*
D(__co_dev* p) {
  return reinterpret_cast<co_dev_t*>(p);
}
can_net_t*
N(__can_net* p) {
  return reinterpret_cast<can_net_t*>(p);
}
co_nmt_t*
M(__co_nmt* p) {
  return reinterpret_cast<co_nmt_t*>(p);
}

std::string
Hex(const uint8_t* p, size_t n) {
  std::string s;
  char b[3];
  for (size_t i = 0; i < n; i++) {
    snprintf(b, sizeof(b), "%02x", p[i]);
    s += b;
  }
  return s;
}

std::vector<uint8_t>
Unhex(const std::string& s) {
  std::vector<uint8_t> v;
  for (size_t i = 0; i + 1 < s.size(); i += 2)
    v.push_back(static_cast<uint8_t>(std::strtoul(s.substr(i, 2).c_str(), nullptr, 16)));
  return v;
}

std::vector<std::string>
Split(const std::string& s, char sep) {
  std::vector<std::string> out;
  std::stringstream ss(s);
  std::string item;
  while (std::getline(ss, item, sep))
    if (!item.empty()) out.push_back(item);
  return out;
}

void
Out(const std::string& line) {
  std::fputs(line.c_str(), stdout);
  std::fputc('\n', stdout);
  std::fflush(stdout);
}

// The value of a sub-object as the Object Dictionary holds it, in the CANopen
// (little-endian) encoding, written by lely's co_val_write().
bool
SubBytes(co_dev_t* dev, uint16_t idx, uint8_t sub, std::string& hex) {
  co_sub_t* s = co_dev_find_sub(dev, idx, sub);
  if (!s) return false;
  co_unsigned16_t type = co_sub_get_type(s);
  const void* val = co_sub_get_val(s);
  size_t n = co_val_write(type, val, nullptr, nullptr);
  std::vector<uint8_t> buf(n);
  if (n) co_val_write(type, val, buf.data(), buf.data() + n);
  hex = Hex(buf.data(), buf.size());
  return true;
}

const char*
StateName(canopen::NmtState st) {
  switch (static_cast<uint8_t>(st) & 0x7f) {
    case 0x00: return "bootup";
    case 0x04: return "stopped";
    case 0x05: return "operational";
    case 0x7f: return "pre-operational";
    default: return "unknown";
  }
}

// The event loop, timers and CAN channel shared by every command.
struct Runtime {
  io::IoGuard io_guard;
  io::Context ctx;
  io::Poll poll{ctx};
  ev::Loop loop{poll.get_poll()};
  ev::Executor exec{loop.get_executor()};
  io::Timer timer{poll, exec, CLOCK_MONOTONIC};
  io::Timer deadline{poll, exec, CLOCK_MONOTONIC};
  io::Timer ticker{poll, exec, CLOCK_MONOTONIC};
  io::CanController ctrl;
  io::CanChannel chan{poll, exec};
  io::SignalSet sigset{poll, exec};

  explicit Runtime(const char* iface) : ctrl(iface) {
    chan.open(ctrl);
    sigset.insert(SIGINT);
    sigset.insert(SIGTERM);
    sigset.submit_wait([this](int) { Stop(); });
  }

  void
  StopAfter(milliseconds ms) {
    deadline.settime(ms);
    deadline.submit_wait([this](int, std::error_code) { Stop(); });
  }

  void
  Stop() {
    ctx.shutdown();
  }

  void
  Run() {
    loop.run();
  }
};

// ── device ───────────────────────────────────────────────────────────────

class Device : public canopen::BasicSlave {
 public:
  using BasicSlave::BasicSlave;

  void
  Tick() {
    auto v = Read<uint32_t>(0x2002, 0);
    Write(0x2002, 0, static_cast<uint32_t>(v + 1));
    // Triggers the event-driven TPDOs this sub-object is mapped into.
    SetEvent(0x2002, 0);
  }

  void
  Emcy(uint16_t eec, uint8_t er, const std::vector<uint8_t>& data) {
    uint8_t msef[5] = {0, 0, 0, 0, 0};
    for (size_t i = 0; i < data.size() && i < 5; i++) msef[i] = data[i];
    Error(eec, er, msef);
  }

  void
  Dump(const std::string& key) {
    unsigned idx = 0, sub = 0;
    if (std::sscanf(key.c_str(), "%x:%x", &idx, &sub) != 2) return;
    std::string hex;
    char head[32];
    snprintf(head, sizeof(head), "DUMP %04X:%02X ", idx, sub);
    Out(std::string(head) + (SubBytes(D(dev()), idx, sub, hex) ? hex : "ERR"));
  }
};

int
CmdDevice(char** a) {
  Runtime rt(a[0]);
  int node = std::atoi(a[1]);
  Device dev(rt.timer, rt.chan, a[2], "", node);
  int duration = std::atoi(a[3]);
  int tick = std::atoi(a[4]);
  std::string emcy = a[5];
  int emcy_after = std::atoi(a[6]);
  std::string dump = a[7];

  rt.StopAfter(milliseconds(duration));
  std::function<void()> arm;
  if (tick > 0) {
    arm = [&]() {
      rt.ticker.settime(milliseconds(tick));
      rt.ticker.submit_wait([&](int, std::error_code ec) {
        if (ec) return;
        dev.Tick();
        arm();
      });
    };
    arm();
  }
  io::Timer emcy_timer(rt.poll, rt.exec, CLOCK_MONOTONIC);
  if (emcy != "-") {
    auto f = Split(emcy, ':');
    uint16_t eec = static_cast<uint16_t>(std::strtoul(f.at(0).c_str(), nullptr, 16));
    uint8_t er = static_cast<uint8_t>(std::strtoul(f.at(1).c_str(), nullptr, 16));
    auto data = Unhex(f.size() > 2 ? f[2] : "");
    emcy_timer.settime(milliseconds(emcy_after > 0 ? emcy_after : 1));
    emcy_timer.submit_wait([&, eec, er, data](int, std::error_code ec) {
      if (!ec) dev.Emcy(eec, er, data);
    });
  }
  dev.Reset();  // boot: NMT 'reset node', boot-up, pre-operational
  rt.exec.post([]() { Out("READY"); });
  rt.Run();
  if (dump != "-")
    for (auto& key : Split(dump, ',')) dev.Dump(key);
  Out("END");
  return 0;
}

// ── master ───────────────────────────────────────────────────────────────

// The NMT master of lely-core (0x1F80 bit 0 in the master DCF). Commands
// other than listen do not boot it (no Reset()): a booting CiA 302 master
// resets every node on the network first. They use the coapp Sdo client
// and the C services directly.
class Master : public canopen::AsyncMaster {
 public:
  using AsyncMaster::AsyncMaster;
  using Node::dev;
  using Node::net;
  using Node::nmt;

 protected:
  void
  OnState(uint8_t id, canopen::NmtState st) noexcept override {
    AsyncMaster::OnState(id, st);
    if (id == co_dev_get_id(D(dev()))) return;
    if (st == canopen::NmtState::BOOTUP)
      Out("BOOTUP " + std::to_string(id));
    else
      Out("STATE " + std::to_string(id) + " " + StateName(st));
  }

  void
  OnEmcy(uint8_t id, uint16_t eec, uint8_t er, uint8_t msef[5]) noexcept override {
    AsyncMaster::OnEmcy(id, eec, er, msef);
    char head[64];
    snprintf(head, sizeof(head), "EMCY %d %04X %02X ", id, eec, er);
    Out(std::string(head) + Hex(msef, 5));
  }
};

void
SdoResult(std::error_code ec, const std::string& ok) {
  if (ec) {
    char b[32];
    snprintf(b, sizeof(b), "ERROR %08X ", static_cast<unsigned>(ec.value()));
    Out(std::string(b) + ec.message());
  } else {
    Out(ok);
  }
}

int
CmdUpload(char** a) {
  Runtime rt(a[0]);
  Master m(rt.timer, rt.chan, a[1], "", 0x7f);
  uint8_t node = static_cast<uint8_t>(std::atoi(a[2]));
  uint16_t idx = static_cast<uint16_t>(std::strtoul(a[3], nullptr, 16));
  uint8_t sub = static_cast<uint8_t>(std::strtoul(a[4], nullptr, 0));
  bool block = std::atoi(a[5]) != 0;
  milliseconds timeout(std::atoi(a[6]));
  canopen::Sdo sdo(m.net(), node);
  sdo.SubmitUpload<std::vector<uint8_t>>(
      rt.exec, idx, sub,
      [&](uint8_t, uint16_t, uint8_t, std::error_code ec, std::vector<uint8_t> value) {
        SdoResult(ec, "RESULT " + Hex(value.data(), value.size()));
        rt.Stop();
      },
      block, timeout);
  rt.StopAfter(timeout + milliseconds(1000));
  rt.Run();
  return 0;
}

int
CmdDownload(char** a) {
  Runtime rt(a[0]);
  Master m(rt.timer, rt.chan, a[1], "", 0x7f);
  uint8_t node = static_cast<uint8_t>(std::atoi(a[2]));
  uint16_t idx = static_cast<uint16_t>(std::strtoul(a[3], nullptr, 16));
  uint8_t sub = static_cast<uint8_t>(std::strtoul(a[4], nullptr, 0));
  bool block = std::atoi(a[5]) != 0;
  milliseconds timeout(std::atoi(a[6]));
  auto data = Unhex(a[7]);
  canopen::Sdo sdo(m.net(), node);
  sdo.SubmitDownload(
      rt.exec, idx, sub, std::move(data),
      [&](uint8_t, uint16_t, uint8_t, std::error_code ec) {
        SdoResult(ec, "RESULT");
        rt.Stop();
      },
      block, timeout);
  rt.StopAfter(timeout + milliseconds(1000));
  rt.Run();
  return 0;
}

// nmt: lely-core sends NMT commands only as a booted NMT master
// (co_nmt_cs_req() needs the master flag, which the NMT start-up sets), and a
// booting CiA 302 master first sends NMT 'reset communication' to all nodes.
// The command follows 500 ms later, when the nodes have booted again.
int
CmdNmt(char** a) {
  Runtime rt(a[0]);
  Master m(rt.timer, rt.chan, a[1], "", 0x7f);
  uint8_t node = static_cast<uint8_t>(std::atoi(a[2]));
  uint8_t cs = static_cast<uint8_t>(std::strtoul(a[3], nullptr, 0));
  bool ok = false;
  io::Timer later(rt.poll, rt.exec, CLOCK_MONOTONIC);
  m.Reset();
  later.settime(milliseconds(500));
  later.submit_wait([&](int, std::error_code ec) {
    if (ec) return;
    ok = co_nmt_cs_req(M(m.nmt()), cs, node) != -1;
    rt.StopAfter(milliseconds(100));  // let the channel send the frame
  });
  rt.StopAfter(milliseconds(2000));
  rt.Run();
  Out(ok ? "RESULT" : "ERROR 08000000 co_nmt_cs_req failed");
  return 0;
}

int
CmdListen(char** a) {
  Runtime rt(a[0]);
  Master m(rt.timer, rt.chan, a[1], "", 0x7f);
  rt.StopAfter(milliseconds(std::atoi(a[2])));
  // Boot the master: CiA 302 NMT master start-up (NMT 'reset communication'
  // to all nodes), then the heartbeat (0x1016) and EMCY (0x1028) consumers
  // of the master DCF run and report through OnState() and OnEmcy().
  m.Reset();
  rt.exec.post([]() { Out("READY"); });
  rt.Run();
  return 0;
}

struct RpdoCtx {
  Master* m;
  std::vector<std::pair<uint32_t, uint32_t>> map;  // remote (idx<<8|sub) -> local (idx<<8|sub)
  uint32_t cobid;
};

void
OnRpdo(co_rpdo_t*, co_unsigned32_t ac, const void*, size_t, void* data) {
  auto* c = static_cast<RpdoCtx*>(data);
  if (ac) {
    char b[48];
    snprintf(b, sizeof(b), "PDOERROR %08X", ac);
    Out(b);
    return;
  }
  char head[32];
  snprintf(head, sizeof(head), "PDO %03X", c->cobid);
  std::string line = head;
  for (auto& e : c->map) {
    std::string hex;
    SubBytes(D(c->m->dev()), e.second >> 8, e.second & 0xff, hex);
    char k[16];
    snprintf(k, sizeof(k), " %04X:%02X=", e.first >> 8, e.first & 0xff);
    line += k + hex;
  }
  Out(line);
}

// pdo-receive: read the TPDO's COB-ID and mapping from the node with the
// coapp Sdo client, configure Receive-PDO 1 of the master (0x1400/0x1600) to
// map the same objects into its scratch objects 0x5F00-0x5F03 (UNSIGNED8/16/
// 32/64 arrays) and let lely's RPDO service unpack every PDO.
int
CmdPdoReceive(char** a) {
  Runtime rt(a[0]);
  Master m(rt.timer, rt.chan, a[1], "", 0x7f);
  uint8_t node = static_cast<uint8_t>(std::atoi(a[2]));
  int tpdo = std::atoi(a[3]);
  rt.StopAfter(milliseconds(std::atoi(a[4])));
  canopen::Sdo sdo(m.net(), node);
  milliseconds t(1000);
  auto ctx = std::make_unique<RpdoCtx>();
  ctx->m = &m;
  co_rpdo_t* rpdo = nullptr;
  uint16_t com = static_cast<uint16_t>(0x1800 + tpdo - 1);
  uint16_t map = static_cast<uint16_t>(0x1A00 + tpdo - 1);
  std::vector<uint32_t> entries;
  uint8_t count = 0;
  std::function<void(uint8_t)> read_entry;
  auto fail = [&](const std::string& what, std::error_code ec) {
    char b[32];
    snprintf(b, sizeof(b), "ERROR %08X ", static_cast<unsigned>(ec.value()));
    Out(std::string(b) + what + ": " + ec.message());
    rt.Stop();
  };
  auto configure = [&]() {
    co_dev_t* dev = D(m.dev());
    co_dev_set_val_u32(dev, 0x1400, 1, ctx->cobid | 0x80000000u);
    co_dev_set_val_u8(dev, 0x1400, 2, 0xff);
    int used[4] = {0, 0, 0, 0};
    co_dev_set_val_u8(dev, 0x1600, 0, 0);
    uint8_t n = 0;
    for (uint32_t e : entries) {
      uint8_t bits = e & 0xff;
      int kind = bits == 8 ? 0 : bits == 16 ? 1 : bits == 32 ? 2 : bits == 64 ? 3 : -1;
      if (kind < 0) {
        Out("ERROR 08000000 mapped object of " + std::to_string(bits) + " bits");
        rt.Stop();
        return;
      }
      uint16_t lidx = static_cast<uint16_t>(0x5F00 + kind);
      uint8_t lsub = static_cast<uint8_t>(++used[kind]);
      ctx->map.emplace_back(e >> 8, static_cast<uint32_t>(lidx) << 8 | lsub);
      co_dev_set_val_u32(dev, 0x1600, ++n, static_cast<uint32_t>(lidx) << 16 | lsub << 8 | bits);
    }
    co_dev_set_val_u8(dev, 0x1600, 0, n);
    co_dev_set_val_u32(dev, 0x1400, 1, ctx->cobid);
    rpdo = co_rpdo_create(N(m.net()), dev, 1);
    if (!rpdo || co_rpdo_start(rpdo) == -1) {
      Out("ERROR 08000000 co_rpdo_create/start failed");
      rt.Stop();
      return;
    }
    co_rpdo_set_ind(rpdo, &OnRpdo, ctx.get());
    Out("READY");
  };
  read_entry = [&](uint8_t i) {
    if (i > count) {
      configure();
      return;
    }
    sdo.SubmitUpload<uint32_t>(
        rt.exec, map, i,
        [&, i](uint8_t, uint16_t, uint8_t, std::error_code ec, uint32_t v) {
          if (ec) return fail("mapping entry", ec);
          if (v >> 16) entries.push_back(v);
          read_entry(static_cast<uint8_t>(i + 1));
        },
        false, t);
  };
  sdo.SubmitUpload<uint32_t>(
      rt.exec, com, 1,
      [&](uint8_t, uint16_t, uint8_t, std::error_code ec, uint32_t v) {
        if (ec) return fail("COB-ID", ec);
        if (v & 0x80000000u) {
          Out("ERROR 08000024 TPDO is not valid");
          rt.Stop();
          return;
        }
        ctx->cobid = v & 0x7ff;
        sdo.SubmitUpload<uint8_t>(
            rt.exec, map, 0,
            [&](uint8_t, uint16_t, uint8_t, std::error_code ec2, uint8_t n) {
              if (ec2) return fail("mapping count", ec2);
              count = n;
              read_entry(1);
            },
            false, t);
      },
      false, t);
  rt.Run();
  if (rpdo) co_rpdo_destroy(rpdo);
  return 0;
}

}  // namespace

int
main(int argc, char** argv) {
  struct {
    const char* name;
    int args;
    int (*fn)(char**);
  } cmds[] = {
      {"device", 8, CmdDevice},      {"upload", 7, CmdUpload},       {"download", 8, CmdDownload},
      {"nmt", 4, CmdNmt},            {"listen", 3, CmdListen},       {"pdo-receive", 5, CmdPdoReceive},
  };
  if (argc >= 2) {
    for (auto& c : cmds) {
      if (std::strcmp(argv[1], c.name) == 0 && argc == c.args + 2) {
        try {
          return c.fn(argv + 2);
        } catch (std::exception& e) {
          Out(std::string("FATAL ") + e.what());
          return 1;
        }
      }
    }
  }
  std::fprintf(stderr, "usage: lely-coi device|upload|download|nmt|listen|pdo-receive ...\n");
  return 2;
}
