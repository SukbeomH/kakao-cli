package net.zetetic.database;
public interface DatabaseErrorHandler { void onCorruption(Object db,Exception error); }
