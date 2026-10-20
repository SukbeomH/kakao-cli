package kakao.cli;

import java.io.*;
import java.lang.reflect.*;
import java.nio.charset.StandardCharsets;
import java.util.*;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;

/** 기기 내부 전용. 키/DB 사본/복구/Room helper/쓰기 SQL을 제공하지 않는다.
 * 형식 참고: kakaotalk-bridge @097a9b8 ProfileData (MIT, NOTICE).
 * SQLCipher는 수정하지 않은 Zetetic 공식 4.10.0만 사용한다.
 */
public final class ProfileReader {
    static final String ROOT = "/data/user/0/com.kakao.talk/";
    static final String DIR = "/data/local/tmp/kakao-cli-profile-helper/";
    static final int MAX = 4 * 1024 * 1024;
    static final int ROWS = 50000;

    static final class Wire {
        final byte[] bytes; int at; final int end;
        Wire(byte[] bytes) { this(bytes, 0, bytes.length); }
        Wire(byte[] b, int a, int e) { bytes=b; at=a; end=e; }
        long integer() {
            long result=0;
            for (int shift=0; shift<=63; shift+=7) {
                if (at>=end) throw new IllegalArgumentException();
                int part=bytes[at++] & 255;
                if (shift==63 && part>1) throw new IllegalArgumentException();
                result |= (long)(part & 127) << shift;
                if ((part & 128)==0) return result;
            }
            throw new IllegalArgumentException();
        }
        Wire block() {
            long n=integer();
            if (n<0 || n>end-at) throw new IllegalArgumentException();
            Wire child=new Wire(bytes,at,at+(int)n); at=child.end; return child;
        }
        byte[] copy() { return Arrays.copyOfRange(bytes,at,end); }
        String text() { return new String(bytes,at,end-at,StandardCharsets.UTF_8); }
        void skip(int tag) {
            switch (tag & 7) {
                case 0: integer(); break;
                case 1: if (end-at<8) throw new IllegalArgumentException(); at+=8; break;
                case 2: block(); break;
                case 5: if (end-at<4) throw new IllegalArgumentException(); at+=4; break;
                default: throw new IllegalArgumentException();
            }
        }
    }
    static byte[] key(byte[] prefs) throws Exception {
        if (prefs.length==0 || prefs.length>MAX) throw new IllegalArgumentException();
        Long salt=null; byte[] fallback=null;
        Set<String> seen=new HashSet<>();
        try {
            Wire root=new Wire(prefs);
            while (root.at<root.end) {
                int tag=(int)root.integer();
                if (tag<=0) throw new IllegalArgumentException();
                if (tag!=10) { root.skip(tag); continue; }
                Wire entry=root.block(), value=null; String name=null;
                while (entry.at<entry.end) {
                    int field=(int)entry.integer();
                    if (field==10) {
                        if (name!=null) throw new IllegalArgumentException();
                        name=entry.block().text();
                    } else if (field==18) {
                        if (value!=null) throw new IllegalArgumentException();
                        value=entry.block();
                    } else { if (field<=0) throw new IllegalArgumentException(); entry.skip(field); }
                }
                if (!"userDbPassPhraseSalt".equals(name) && !"userDbPassPhraseWhenFailed".equals(name)) continue;
                if (!seen.add(name) || value==null) throw new IllegalArgumentException();
                if (name.equals("userDbPassPhraseSalt")) {
                    if (value.integer()!=32) throw new IllegalArgumentException();
                    salt=value.integer();
                } else {
                    if (value.integer()!=42) throw new IllegalArgumentException();
                    fallback=value.block().copy();
                }
                if (value.at!=value.end) throw new IllegalArgumentException();
            }
            if (fallback!=null && fallback.length>0) {
                byte[] compact=new byte[fallback.length]; int n=0;
                try {
                    for (byte c:fallback) if (c!=10 && c!=13 && c!=32 && c!=9) compact[n++]=c;
                    byte[] encoded=Arrays.copyOf(compact,n);
                    try {
                        byte[] result=Base64.getDecoder().decode(encoded);
                        if (result.length!=32) { Arrays.fill(result,(byte)0); throw new IllegalArgumentException(); }
                        return result;
                    } finally { Arrays.fill(encoded,(byte)0); }
                } finally { Arrays.fill(compact,(byte)0); }
            }
            if (salt==null || salt==0) throw new IllegalArgumentException();
            byte[] raw=("se"+salt+"ed").getBytes(StandardCharsets.UTF_8);
            byte[] padded=Arrays.copyOf(raw,16);
            // 앱 파일 형식의 공개 상수. 계정 비밀번호/개인키가 아니다.
            char[] password={4,15,81,123,77,5,23,99,2,111,10,31,54,29,109,97};
            PBEKeySpec spec=new PBEKeySpec(password,padded,4096,256);
            try { return SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded(); }
            finally { spec.clearPassword(); Arrays.fill(password,'\0'); Arrays.fill(raw,(byte)0); Arrays.fill(padded,(byte)0); }
        } finally { if (fallback!=null) Arrays.fill(fallback,(byte)0); }
    }
    static byte[] preferenceBytes() throws Exception {
        File file=new File(ROOT+"files/datastore/Feature_DataStore.pref.preferences_pb");
        long length=file.length();
        if (length<1 || length>MAX) throw new IOException();
        byte[] bytes=new byte[(int)length]; int at=0;
        try (InputStream in=new FileInputStream(file)) {
            while (at<bytes.length) { int n=in.read(bytes,at,bytes.length-at); if (n<0) throw new EOFException(); at+=n; }
            if (in.read()!=-1) throw new IOException();
            return bytes;
        } catch (Throwable e) { Arrays.fill(bytes,(byte)0); throw e; }
    }
    static Object invoke(Object object,String method,Class<?>[] types,Object... args) throws Exception {
        return object.getClass().getMethod(method,types).invoke(object,args);
    }
    static final Class<?>[] NONE=new Class<?>[0];
    static String string(Object cursor,int column) throws Exception {
        return (String)invoke(cursor,"getString",new Class<?>[]{int.class},column);
    }
    static long number(Object cursor,int column) throws Exception {
        return ((Number)invoke(cursor,"getLong",new Class<?>[]{int.class},column)).longValue();
    }
    static String name(String nickname,String friend,String contact,long relation) {
        if (relation!=9) {
            if (friend!=null && !friend.trim().isEmpty()) return friend;
            if (contact!=null && !contact.trim().isEmpty()) return contact;
        }
        return nickname==null ? "" : nickname;
    }
    static String quote(String text) {
        StringBuilder out=new StringBuilder("\"");
        for (int i=0;i<text.length();i++) {
            char c=text.charAt(i);
            if (c=='"' || c=='\\') out.append('\\').append(c);
            else if (c<32 || c==0x2028 || c==0x2029) out.append(String.format(Locale.ROOT,"\\u%04x",(int)c));
            else out.append(c);
        }
        return out.append('"').toString();
    }
    static Object query(Object db,String sql) throws Exception {
        if (!sql.startsWith("SELECT ")) throw new SecurityException();
        return invoke(db,"rawQuery",new Class<?>[]{String.class,String[].class},sql,null);
    }
    static String rows(Object db,boolean channel) throws Exception {
        Object cursor=query(db,channel ? "SELECT id,chat_id,name FROM talk_channel LIMIT 50001" :
                "SELECT id,nickname,friend_nickname,contact_name,relation FROM user LIMIT 50001");
        StringBuilder out=new StringBuilder("["); int count=0;
        try {
            while ((Boolean)invoke(cursor,"moveToNext",NONE)) {
                if (++count>ROWS) throw new IllegalArgumentException();
                String label=channel ? string(cursor,2) : name(string(cursor,1),string(cursor,2),string(cursor,3),number(cursor,4));
                if (label==null || label.trim().isEmpty()) continue;
                if (label.length()>4096) throw new IllegalArgumentException();
                if (out.length()>1) out.append(',');
                out.append("{\"id\":").append(number(cursor,0));
                if (channel) out.append(",\"chat_id\":").append(number(cursor,1));
                out.append(",\"name\":").append(quote(label)).append('}');
                if (out.length()>4*1024*1024) throw new IllegalArgumentException();
            }
            return out.append(']').toString();
        } finally { invoke(cursor,"close",NONE); }
    }
    static boolean loggingDisabled(Object level) {
        // SQLCipher의 프로세스 로그 설정은 DB 파일을 쓰지 않고 정수0을 반환한다.
        return "0".equals(String.valueOf(level)) || "NONE".equalsIgnoreCase(String.valueOf(level));
    }
    static void noDump() throws Exception {
        // helper 프로세스만 dump 비허용. 서비스/기기의 설정을 변경하지 않는다.
        Class<?> os=Class.forName("android.system.Os");
        os.getMethod("prctl",int.class,long.class,long.class,long.class,long.class).invoke(null,4,0L,0L,0L,0L);
    }
    static Object openReadOnly(Class<?> database,byte[] password,Class<?> handler,Class<?> hookType,Object hook) throws Exception {
        Object noRecovery=Proxy.newProxyInstance(handler.getClassLoader(),new Class<?>[]{handler},(proxy,method,values)->{
            throw new SecurityException("read_only_corruption");
        });
        int flags=database.getField("OPEN_READONLY").getInt(null) | database.getField("NO_LOCALIZED_COLLATORS").getInt(null);
        Class<?> factory=Class.forName("net.zetetic.database.sqlcipher.SQLiteDatabase$CursorFactory");
        Object db=database.getMethod("openDatabase",String.class,byte[].class,factory,int.class,handler,hookType)
                .invoke(null,ROOT+"databases/crypto_user_database",password,null,flags,noRecovery,hook);
        if (!(Boolean)invoke(db,"isReadOnly",NONE)) {
            invoke(db,"close",NONE); throw new SecurityException();
        }
        return db;
    }
    public static void main(String[] args) {
        // 예외/스택트레이스/키 자료가 stderr로 나가지 않게 한다.
        try { System.setErr(new PrintStream(new FileOutputStream("/dev/null"))); }
        catch (Throwable ignored) { return; }
        Object db=null; byte[] prefs=null,password=null; String result=null,stage="isolation";
        try {
            if (args.length!=0) throw new IllegalArgumentException();
            noDump();
            stage="library";
            System.load(DIR+"libsqlcipher.so");
            Class<?> target=Class.forName("net.zetetic.database.LogTarget");
            Object noop=Class.forName("net.zetetic.database.NoopTarget").getConstructor().newInstance();
            Class.forName("net.zetetic.database.Logger").getMethod("setTarget",target).invoke(null,noop);
            Class<?> database=Class.forName("net.zetetic.database.sqlcipher.SQLiteDatabase");
            Class<?> handler=Class.forName("net.zetetic.database.DatabaseErrorHandler");
            Class<?> hookType=Class.forName("net.zetetic.database.sqlcipher.SQLiteDatabaseHook");
            Object hook=Proxy.newProxyInstance(hookType.getClassLoader(),new Class<?>[]{hookType},(proxy,method,values)->{
                if (method.getName().equals("preKey")) {
                    Object connection=values[0];
                    Object level=invoke(connection,"executeForString",new Class<?>[]{String.class,Object[].class,Class.forName("android.os.CancellationSignal")},"PRAGMA cipher_log_level=NONE",null,null);
                    if (!loggingDisabled(level)) throw new SecurityException("logging_enabled");
                }
                return null;
            });
            stage="preferences"; prefs=preferenceBytes(); password=key(prefs);
            Arrays.fill(prefs,(byte)0); prefs=null;
            stage="open_readonly";
            File path=new File(ROOT+"databases/crypto_user_database");
            if (!path.isFile()) throw new IOException();
            db=openReadOnly(database,password,handler,hookType,hook);
            stage="schema";
            Object check=query(db,"SELECT id,nickname,friend_nickname,contact_name,relation FROM user LIMIT 0"); invoke(check,"close",NONE);
            check=query(db,"SELECT id,chat_id,name FROM talk_channel LIMIT 0"); invoke(check,"close",NONE);
            stage="query";
            result="{\"ok\":true,\"read_only\":true,\"users\":"+rows(db,false)+",\"channels\":"+rows(db,true)+"}";
        } catch (Throwable error) {
            Throwable cause=error;
            for (int i=0;i<8 && cause.getCause()!=null;i++) cause=cause.getCause();
            String type=cause.getClass().getName();
            String kind=type.contains("SQLite") ? "sqlite" : (cause instanceof SecurityException ? "security" :
                    (cause instanceof ReflectiveOperationException ? "reflection" : (cause instanceof LinkageError ? "native" : "other")));
            result="{\"ok\":false,\"error\":\"profile_unavailable\",\"stage\":"+quote(stage)+",\"kind\":"+quote(kind)+"}";
        } finally {
            try { if (db!=null) invoke(db,"close",NONE); } catch (Throwable ignored) { result="{\"ok\":false,\"error\":\"profile_unavailable\",\"stage\":\"close\"}"; }
            if (prefs!=null) Arrays.fill(prefs,(byte)0);
            if (password!=null) Arrays.fill(password,(byte)0);
        }
        // DB를 닫고 키 버퍼를 비운 뒤 조회 결과만 메모리 파이프로 전달한다.
        if (result!=null) System.out.println(result);
    }
}
