package kakao.cli;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import net.zetetic.database.sqlcipher.SQLiteDatabase;
import net.zetetic.database.sqlcipher.SQLiteDatabaseHook;
import net.zetetic.database.DatabaseErrorHandler;

public class ProfileReaderTest {
    static void check(boolean ok) { if (!ok) throw new AssertionError("synthetic_check_failed"); }
    static byte[] cat(byte[]... chunks) throws Exception {
        ByteArrayOutputStream out=new ByteArrayOutputStream(); for(byte[] b:chunks) out.write(b);return out.toByteArray();
    }
    static byte[] var(long n) {
        ByteArrayOutputStream out=new ByteArrayOutputStream();
        while((n & ~127L)!=0) { out.write(((int)n & 127)|128);n>>>=7; }out.write((int)n);return out.toByteArray();
    }
    static byte[] block(byte[] v) throws Exception { return cat(var(v.length),v); }
    static byte[] entry(String name,byte[] v) throws Exception {
        return cat(new byte[]{10},block(cat(new byte[]{10},block(name.getBytes("UTF-8")),new byte[]{18},block(v))));
    }
    static String hex(byte[] bytes) { StringBuilder s=new StringBuilder();for(byte b:bytes)s.append(String.format("%02x",b&255));return s.toString(); }
    public static void main(String[] args) throws Exception {
        String test=args[0];
        if(test.equals("fallback")) {
            byte[] fake=new byte[32];Arrays.fill(fake,(byte)7);
            byte[] prefs=entry("userDbPassPhraseWhenFailed",cat(new byte[]{42},block(Base64.getEncoder().encode(fake))));
            check(Arrays.equals(ProfileReader.key(prefs),fake));
        } else if(test.equals("salt")) {
            byte[] prefs=entry("userDbPassPhraseSalt",cat(new byte[]{32},var(12345)));
            check(hex(ProfileReader.key(prefs)).equals(args[1]));
        } else if(test.equals("invalid")) {
            byte[] one=entry("userDbPassPhraseSalt",new byte[]{32,1});
            byte[][] bad={new byte[0],new byte[]{10,100},cat(one,one),entry("userDbPassPhraseSalt",new byte[]{42,0}),entry("userDbPassPhraseWhenFailed",new byte[]{42,1,33})};
            for(byte[] b:bad) { boolean rejected=false; try{ProfileReader.key(b);}catch(Exception e){rejected=true;}check(rejected); }
        } else if(test.equals("names")) {
            check(ProfileReader.name("fake-nick","fake-friend","fake-contact",1).equals("fake-friend"));
            check(ProfileReader.name("fake-nick","","fake-contact",1).equals("fake-contact"));
            check(ProfileReader.name("fake-nick","fake-friend","fake-contact",9).equals("fake-nick"));
            check(ProfileReader.quote("a\n\"b").equals("\"a\\u000a\\\"b\""));
        } else if(test.equals("logging")) {
            check(ProfileReader.loggingDisabled("0"));check(ProfileReader.loggingDisabled("NONE"));
            check(!ProfileReader.loggingDisabled("8"));check(!ProfileReader.loggingDisabled(null));
        } else if(test.equals("readonly")) {
            Object db=ProfileReader.openReadOnly(SQLiteDatabase.class,new byte[32],DatabaseErrorHandler.class,SQLiteDatabaseHook.class,null);
            check(db!=null && SQLiteDatabase.flags==17);
            check(SQLiteDatabase.path.equals("/data/user/0/com.kakao.talk/databases/crypto_user_database"));
            check(SQLiteDatabase.observed!=null);
        } else if(test.equals("corruption")) {
            SQLiteDatabase.corrupt=true;
            boolean rejected=false;
            try { ProfileReader.openReadOnly(SQLiteDatabase.class,new byte[32],DatabaseErrorHandler.class,SQLiteDatabaseHook.class,null); }
            catch(Exception e) { rejected=true; }
            check(rejected);
        } else if(test.equals("wrong_mode")) {
            SQLiteDatabase.readonly=false; boolean rejected=false;
            try { ProfileReader.openReadOnly(SQLiteDatabase.class,new byte[32],DatabaseErrorHandler.class,SQLiteDatabaseHook.class,null); }
            catch(Exception e) { rejected=true; }
            check(rejected && SQLiteDatabase.closed);
        } else throw new AssertionError();
        System.out.println("synthetic_ok");
    }
}
