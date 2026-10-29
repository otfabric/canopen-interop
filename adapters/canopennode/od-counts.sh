#!/bin/sh
# SPDX-License-Identifier: MIT
#
# od-counts.sh OD.h: add the "count labels" of CANopenNode V4 to an OD.h that
# CANopenEditor generated from an EDS. CANopenNode needs OD_CNT_<label> for
# its communication objects (doc/objectDictionary.md); the labels are a
# CANopenEditor project property (CO_countLabel) that an EDS cannot carry, so
# CANopenEditor leaves them out of an OD generated from an EDS. They are
# derived here from the objects the OD contains, as CANopenEditor would set
# them, and inserted before the include guard's #endif.
set -eu
od="$1"
has() { grep -q "^#define OD_ENTRY_H$1 " "$od"; }
count() { grep -cE "^#define OD_ENTRY_H($1) " "$od" || true; }
{
    echo "/* Count labels added by canopen-interop adapters/canopennode/od-counts.sh */"
    for pair in 1000:NMT 1001:EM 1005:SYNC 1006:SYNC_PROD 1010:STORAGE 1012:TIME 1014:EM_PROD 1016:HB_CONS 1017:HB_PROD 1300:GFC; do
        idx=${pair%%:*} label=${pair#*:}
        if has "$idx"; then echo "#define OD_CNT_$label 1"; fi
    done
    n=$(count '12[0-7][0-9A-F]'); [ "$n" -gt 0 ] && echo "#define OD_CNT_SDO_SRV $n"
    n=$(count '12[89A-F][0-9A-F]'); [ "$n" -gt 0 ] && echo "#define OD_CNT_SDO_CLI $n"
    n=$(count '1[45][0-9A-F][0-9A-F]'); [ "$n" -gt 0 ] && echo "#define OD_CNT_RPDO $n"
    n=$(count '1[89][0-9A-F][0-9A-F]'); [ "$n" -gt 0 ] && echo "#define OD_CNT_TPDO $n"
    true
} > "$od.counts"
awk -v counts="$od.counts" '
    { lines[NR] = $0 }
    /^#endif/ { last = NR }
    END {
        for (i = 1; i <= NR; i++) {
            if (i == last) { while ((getline l < counts) > 0) print l }
            print lines[i]
        }
    }' "$od" > "$od.new"
mv "$od.new" "$od"
rm -f "$od.counts"
