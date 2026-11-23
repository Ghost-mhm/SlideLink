package app.slidelink.remote;

import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;

import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.ECGenParameterSpec;

/**
 * This phone's end-to-end identity key, kept in the Android Keystore (hardware-backed where the phone has it).
 *     The private part never leaves the Keystore; the app only asks it to sign.
 */
final class E2eKeys implements E2e.Signer {
    private static final String ALIAS = "slidelink_e2e_identity";
    private final KeyStore ks;

    E2eKeys() throws Exception {
        ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (!ks.containsAlias(ALIAS)) {
            KeyPairGenerator g = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore");
            g.initialize(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(new ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build());
            g.generateKeyPair();
        }
    }

    @Override public byte[] publicPoint() {
        try {
            return E2e.point(ks.getCertificate(ALIAS).getPublicKey());
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @Override public byte[] signRaw(byte[] data) throws Exception {
        Signature s = Signature.getInstance("SHA256withECDSA");
        s.initSign((PrivateKey) ks.getKey(ALIAS, null));
        s.update(data);
        return E2e.derToRaw(s.sign());
    }
}
