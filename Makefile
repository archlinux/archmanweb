TARGET = convert_txt
CC = gcc
ECPG = ecpg
CPPFLAGS = -MD -MP
CFLAGS = -O3 -g -Wall
LDFLAGS = -lecpg

.PHONY: all
all: $(TARGET)

%.c: %.pgc
	$(ECPG) $<

%.o: %.c
	$(CC) $(CPPFLAGS) $(CFLAGS) -c -o $@ $<

$(TARGET): % : %.o
	$(CC) -o $@ $< $(LDFLAGS)

.PHONY: clean
clean:
	$(RM) *.o *.d
	$(RM) $(TARGET) $(TARGET).c

-include $(TARGET).d
