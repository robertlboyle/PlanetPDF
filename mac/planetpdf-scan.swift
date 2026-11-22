// planetpdf-scan: PlanetPDF's scanner helper on the Mac (2026-10-14).
//
// Apple's ImageCaptureCore, the framework Image Capture and Preview scan with: USB scanners (Apple's
// own drivers or the maker's), network scanners (AirScan) and ones another Mac shares. Python can't
// call it, so PlanetPDF runs this (core/mac_scanner.py) and reads one JSON object per line:
//
//   planetpdf-scan list [--seconds 4]
//       {"scanner": {"id": …, "name": …, "connection": "usb" | "network"}} for each one
//   planetpdf-scan caps --device ID
//       {"caps": {"glass": {"dpis": […], "max": [w, h]}, "feeder": {…, "duplex": true}}}
//   planetpdf-scan scan --device ID --source glass|feeder|feeder-duplex --color color|gray|bw
//                       --dpi 300 --out FOLDER
//       {"page": PATH} for each page as it arrives, then {"done": N}
//
// Errors: {"error": TEXT} and exit code 1. SIGTERM cancels a scan (PlanetPDF's Cancel button).
// Built by scripts/build_mac.py into PlanetPDF.app/Contents/MacOS; from source, core/mac_scanner.py
// compiles it into build/mac-helper with swiftc (Xcode's command-line tools).

import Foundation
import ImageCaptureCore

let flatbedType = 0, feederType = 3  // ICScannerFunctionalUnitType
let pixelTypes = ["bw": 0, "gray": 1, "color": 2]  // ICScannerPixelDataType

func emit(_ object: [String: Any]) {
    if let data = try? JSONSerialization.data(withJSONObject: object) {
        FileHandle.standardOutput.write(data + Data("\n".utf8))
    }
}

let debugging = ProcessInfo.processInfo.environment["PLANETPDF_SCAN_DEBUG"] != nil

func debug(_ text: String) {  // (PLANETPDF_SCAN_DEBUG=1: what ImageCaptureCore said, on stderr)
    if debugging { FileHandle.standardError.write(Data("[scan] \(text)\n".utf8)) }
}

func fail(_ text: String) -> Never {
    emit(["error": text])
    exit(1)
}

func option(_ name: String, _ fallback: String = "") -> String {
    let args = CommandLine.arguments
    if let i = args.firstIndex(of: "--" + name), i + 1 < args.count { return args[i + 1] }
    return fallback
}

func connection(_ device: ICDevice) -> String {
    let place = ((device.transportType ?? "") + " " + (device.locationDescription ?? "")).uppercased()
    return place.contains("USB") ? "usb" : "network"
}

final class Helper: NSObject, ICDeviceBrowserDelegate, ICScannerDeviceDelegate {
    let command: String
    let browser = ICDeviceBrowser()
    var found: [ICScannerDevice] = []
    var target: ICScannerDevice?
    var unitsToRead: [Int] = []
    var started = false  // (the source has been asked for)
    var guessed = false  // (the scanner didn't list its sources: an error selecting one means it has none)
    var requested: Int? = nil  // the source asked for: other selections (the scanner's default) are ignored
    var caps: [String: Any] = [:]
    var pages = 0

    init(command: String) {
        self.command = command
    }

    func start() {
        browser.delegate = self
        let mask = ICDeviceTypeMask.scanner.rawValue | ICDeviceLocationTypeMask.local.rawValue
            | ICDeviceLocationTypeMask.bonjour.rawValue | ICDeviceLocationTypeMask.shared.rawValue
            | ICDeviceLocationTypeMask.remote.rawValue
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(rawValue: mask)!
        browser.start()
        let seconds = Double(option("seconds", command == "list" ? "4" : "15")) ?? 4
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { [self] in
            if command == "list" {
                for scanner in found {
                    emit(["scanner": ["id": scanner.persistentIDString ?? scanner.name ?? "",
                                      "name": scanner.name ?? "Scanner", "connection": connection(scanner)]])
                }
                exit(0)
            } else if target == nil {
                fail("The scanner wasn't found. Check that it's on and connected.")
            }
        }
        if command == "caps" {  // (a scan has no limit: a feeder of pages can take minutes)
            DispatchQueue.main.asyncAfter(deadline: .now() + 45) {
                fail("The scanner didn't say what it can do within 45 seconds.")
            }
        }
    }

    // --- finding the scanner ---

    func deviceBrowser(_ browser: ICDeviceBrowser, didAdd device: ICDevice, moreComing: Bool) {
        guard let scanner = device as? ICScannerDevice else { return }
        debug("found \(scanner.name ?? "?") id=\(scanner.persistentIDString ?? "?") transport=\(scanner.transportType ?? "?")")
        found.append(scanner)
        let wanted = option("device")
        if command != "list", target == nil, scanner.persistentIDString == wanted || scanner.name == wanted {
            target = scanner
            scanner.delegate = self
            scanner.requestOpenSession()
        }
    }

    func deviceBrowser(_ browser: ICDeviceBrowser, didRemove device: ICDevice, moreGoing: Bool) {}

    func didRemove(_ device: ICDevice) {
        if device === target { fail("The scanner was disconnected.") }
    }

    func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {
        debug("session open, error=\(String(describing: error))")
        if let error { fail("The scanner couldn't be opened (\(error.localizedDescription)). It may be in use.") }
        guard let scanner = device as? ICScannerDevice else { return }
        debug("units at open: \(scanner.availableFunctionalUnitTypes)")
        if !scanner.availableFunctionalUnitTypes.isEmpty { begin(scanner) }  // (else when it says it's ready)
    }

    func scannerDeviceDidBecomeAvailable(_ scanner: ICScannerDevice) {
        debug("available; units: \(scanner.availableFunctionalUnitTypes)")
        begin(scanner)
    }

    // Some scanners (the office's Canons over AirScan) never list their sources: they just select
    // their glass. Then the selected one counts as there, and the feeder is tried (an error = none).
    func begin(_ scanner: ICScannerDevice, current: ICScannerFunctionalUnit? = nil) {
        if started { return }
        var available = scanner.availableFunctionalUnitTypes.map { $0.intValue }
        if available.isEmpty, let current {
            available = [flatbedType, feederType]
            guessed = true
            debug("no source list: trying \(available) (selected now: \(current.type.rawValue))")
        }
        if available.isEmpty { return }
        started = true
        if command == "caps" {
            unitsToRead = available.filter { $0 == flatbedType || $0 == feederType }
            if unitsToRead.isEmpty { fail("The scanner offers neither a glass nor a document feeder.") }
            next(scanner, current)
        } else {
            let wanted = option("source", "glass") == "glass" ? flatbedType : feederType
            if !available.contains(wanted) {
                fail(wanted == flatbedType ? "The scanner has no glass." : "The scanner has no document feeder.")
            }
            requested = wanted
            if let current, Int(current.type.rawValue) == wanted {
                use(scanner, current, nil)  // (already the selected one)
            } else {
                select(scanner, wanted)
            }
        }
    }

    func next(_ scanner: ICScannerDevice, _ current: ICScannerFunctionalUnit?) {
        let type = unitsToRead.removeFirst()
        requested = type
        if let current, Int(current.type.rawValue) == type {
            use(scanner, current, nil)
        } else {
            select(scanner, type)
        }
    }

    func device(_ device: ICDevice, didCloseSessionWithError error: Error?) {
        debug("session closed")
    }

    func device(_ device: ICDevice, didReceiveStatusInformation status: [ICDeviceStatus: Any]) {
        debug("status: \(status)")
    }

    func device(_ device: ICDevice, didEncounterError error: Error?) {
        debug("device error: \(String(describing: error))")
    }

    func select(_ scanner: ICScannerDevice, _ type: Int) {
        guard let unitType = ICScannerFunctionalUnitType(rawValue: UInt(type)) else { return }
        requested = type
        scanner.requestSelect(unitType)
    }

    // --- what it can do, then scanning ---

    func scannerDevice(_ scanner: ICScannerDevice, didSelect unit: ICScannerFunctionalUnit, error: Error?) {
        debug("selected unit type \(unit.type.rawValue) (asked \(String(describing: requested))), error=\(String(describing: error))")
        if !started {
            begin(scanner, current: unit)  // (the scanner chose its default before we asked)
            return
        }
        guard let wanted = requested else { return }
        if Int(unit.type.rawValue) != wanted || error != nil {  // (couldn't switch to the one asked for)
            if command == "caps" && guessed {
                finishUnit(scanner, unit)
                return
            }
            if error != nil || Int(unit.type.rawValue) != wanted {
                fail(wanted == flatbedType ? "The scanner has no glass." : "The scanner has no document feeder.")
            }
        }
        use(scanner, unit, error)
    }

    func finishUnit(_ scanner: ICScannerDevice, _ unit: ICScannerFunctionalUnit) {
        if unitsToRead.isEmpty {
            if caps.isEmpty { fail("The scanner offers neither a glass nor a document feeder.") }
            emit(["caps": caps])
            exit(0)
        }
        next(scanner, unit)
    }

    func use(_ scanner: ICScannerDevice, _ unit: ICScannerFunctionalUnit, _ error: Error?) {
        if let error { fail("The scanner couldn't switch to that source (\(error.localizedDescription)).") }
        unit.measurementUnit = ICScannerMeasurementUnit(rawValue: 0)!  // inches
        let size = unit.physicalSize
        if command == "caps" {
            var info: [String: Any] = ["dpis": unit.supportedResolutions.map { $0 },
                                       "max": [size.width, size.height]]
            if let feeder = unit as? ICScannerFunctionalUnitDocumentFeeder {
                info["duplex"] = feeder.supportsDuplexScanning
                caps["feeder"] = info
            } else {
                caps["glass"] = info
            }
            finishUnit(scanner, unit)
            return
        }
        let color = option("color", "color")
        let pixel = pixelTypes[color] ?? 2
        unit.pixelDataType = ICScannerPixelDataType(rawValue: UInt(pixel))!
        unit.bitDepth = ICScannerBitDepth(rawValue: pixel == 0 ? 1 : 8)!
        let wanted = Int(option("dpi", "300")) ?? 300
        let dpis = unit.supportedResolutions.map { $0 }
        unit.resolution = dpis.min(by: { abs($0 - wanted) < abs($1 - wanted) }) ?? wanted
        if let feeder = unit as? ICScannerFunctionalUnitDocumentFeeder {
            feeder.documentType = ICScannerDocumentType(rawValue: 3)!  // US Letter
            feeder.duplexScanningEnabled = option("source") == "feeder-duplex" && feeder.supportsDuplexScanning
            if !feeder.documentLoaded { fail("The document feeder is empty: put the pages in and scan again.") }
        } else {
            unit.scanArea = NSRect(x: 0, y: 0, width: min(8.5, size.width), height: min(11, size.height))
        }
        scanner.transferMode = ICScannerTransferMode(rawValue: 0)!  // into files
        scanner.downloadsDirectory = URL(fileURLWithPath: option("out", NSTemporaryDirectory()))
        scanner.documentName = "scan"
        scanner.documentUTI = "public.jpeg"
        scanner.requestScan()
    }

    func scannerDevice(_ scanner: ICScannerDevice, didScanTo url: URL) {
        pages += 1
        emit(["page": url.path])
    }

    func scannerDevice(_ scanner: ICScannerDevice, didCompleteScanWithError error: Error?) {
        if let error, pages == 0 { fail("The scan didn't work (\(error.localizedDescription)).") }
        emit(["done": pages])
        scanner.requestCloseSession()
        exit(0)
    }
}

let command = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : ""
guard ["list", "caps", "scan"].contains(command) else {
    FileHandle.standardError.write(Data("usage: planetpdf-scan list|caps|scan [options]\n".utf8))
    exit(2)
}
let helper = Helper(command: command)
signal(SIGTERM, SIG_IGN)
let cancel = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
cancel.setEventHandler {
    helper.target?.cancelScan()
    emit(["error": "Canceled."])
    exit(1)
}
cancel.resume()
helper.start()
RunLoop.main.run()
