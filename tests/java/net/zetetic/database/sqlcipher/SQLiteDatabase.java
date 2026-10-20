package net.zetetic.database.sqlcipher;
import net.zetetic.database.DatabaseErrorHandler;
/** 합성 엔진: 실제 DB/SQLCipher를 포함하지 않는다. */
public class SQLiteDatabase {
    public interface CursorFactory { }
    public static final int OPEN_READONLY=1, NO_LOCALIZED_COLLATORS=16;
    public static boolean corrupt=false,readonly=true,closed=false;
    public static DatabaseErrorHandler observed;
    public static String path; public static int flags;
    public static SQLiteDatabase openDatabase(String name,byte[] password,CursorFactory f,int mode,
                                               DatabaseErrorHandler handler,SQLiteDatabaseHook hook) {
        path=name; flags=mode; observed=handler;
        SQLiteDatabase db=new SQLiteDatabase();
        if (corrupt) handler.onCorruption(db,new Exception("FAKE_ONLY"));
        return db;
    }
    public boolean isReadOnly() { return readonly; }
    public void close() { closed=true; }
}
