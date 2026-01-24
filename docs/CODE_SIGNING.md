# Code Signing for PFR Sentinel

## Current Status: ✅ SIGNED

PFR Sentinel is **code signed** with a Certum Open Source Developer certificate.

**Certificate Details:**
- **Publisher:** Open Source Developer, Paul Fox-Reeks
- **Issuer:** Certum Code Signing 2021 CA
- **Valid Until:** January 23, 2027
- **Thumbprint:** `B5E267FE814CD41B883876712CA326C288FB3492`

Users will see "Open Source Developer, Paul Fox-Reeks" as the publisher instead of "Unknown publisher".

## Signing Setup (Certum SimplySign)

This project uses **Certum SimplySign** - a cloud-based HSM solution where the private key is stored securely on Certum's servers.

### Prerequisites
1. **SimplySign Desktop** - Windows app that integrates with cloud HSM
2. **SimplySign Mobile App** - For approving signing requests
3. **Windows SDK** - Provides `signtool.exe`

### Automated Build Signing

The build scripts automatically sign executables:
- `build_sentinel.bat` - Signs `PFRSentinel.exe` after PyInstaller build
- `build_sentinel_installer.bat` - Signs both EXE and installer

**During signing, approve the request in your SimplySign mobile app.**

### Manual Signing

```powershell
# Sign executable
signtool sign /sha1 B5E267FE814CD41B883876712CA326C288FB3492 /tr http://time.certum.pl /td SHA256 /fd SHA256 /d "PFR Sentinel" "dist\PFRSentinel\PFRSentinel.exe"

# Sign installer
signtool sign /sha1 B5E267FE814CD41B883876712CA326C288FB3492 /tr http://time.certum.pl /td SHA256 /fd SHA256 /d "PFR Sentinel Setup" "installer\dist\PFR Sentinel-X.X.X-setup.exe"
```

### Verify Signature

```powershell
Get-AuthenticodeSignature "dist\PFRSentinel\PFRSentinel.exe" | Format-List Status, SignerCertificate
```

---

## Background: Why Code Signing Matters

Windows SmartScreen checks for:
1. **Digital signature** - Proves the publisher's identity
2. **Reputation** - Tracks how many users have downloaded the file
3. **Certificate validity** - Ensures the signature is from a trusted authority

## What Users Should Do

Users can safely bypass this warning:

1. Click **"More info"** link
2. Click **"Run anyway"** button
3. The application will start normally

Once enough users download and run the app without reporting issues, Windows SmartScreen will build a reputation and stop showing the warning (this takes weeks/months and many downloads).

## Code Signing Options

### Option 1: Accept the Warning (Current - FREE)
**Cost:** $0  
**Pros:**
- No cost
- No annual renewals
- Still fully functional

**Cons:**
- Users see warning on first run
- May reduce trust for some users
- Requires clicking "More info" → "Run anyway"

**Best for:** Open source projects, personal use, small user base

---

### Option 2: Get a Code Signing Certificate (EXPENSIVE)

**Cost:** $100-$500/year  
**Pros:**
- No SmartScreen warning
- Builds user trust
- Professional appearance
- Protects against tampering

**Cons:**
- Expensive recurring cost
- Requires identity verification
- Annual renewal required
- Takes time to obtain

**Providers:**
- **DigiCert** (~$474/year) - Most trusted
- **Sectigo (Comodo)** (~$199/year) - Good value
- **SSL.com** (~$199/year) - Affordable option

**Process:**
1. Purchase certificate from provider
2. Verify your identity (business or personal)
3. Receive certificate file (.pfx or .p12)
4. Sign executable with `signtool.exe`:
   ```powershell
   signtool sign /f certificate.pfx /p password /t http://timestamp.digicert.com ASIOverlayWatchDog.exe
   ```

**Best for:** Commercial software, large user base, professional deployments

---

### Option 3: Self-Signed Certificate (NOT RECOMMENDED)

**Cost:** $0  
**Effect:** **Makes warning worse** - Windows shows "Unknown publisher" AND "Untrusted certificate"

Self-signing doesn't help because Windows only trusts certificates from recognized Certificate Authorities (CAs). Users would need to manually import your certificate to their Trusted Publishers store.

**Don't do this** - it's more work for users than just clicking "Run anyway."

---

## Certificate Renewal

The certificate expires **January 23, 2027**. To renew:
1. Log into your Certum account
2. Renew the certificate before expiration
3. Update the thumbprint in build scripts if it changes

---

## Resources

- [Certum SimplySign](https://www.certum.eu/en/cert_offer_SimplySign/)
- [Microsoft SignTool Documentation](https://docs.microsoft.com/en-us/windows/win32/seccrypto/signtool)
- [VirusTotal](https://www.virustotal.com/) - Free malware scanning service
